import { useEffect, useRef, useState } from 'react';
import { api, number, post } from './api';
import { Pitch, possession } from './Pitch';
import './match.css';

const roles = ['goalkeeper', 'defender', 'midfielder', 'forward1', 'forward2'];
const names = ['Goalkeeper', 'Defender', 'Midfielder', 'Forward 1', 'Forward 2'];
const codes = ['GK', 'DEF', 'MID', 'F1', 'F2'];
const clock = (time = 0) => `${Math.floor(time / 60).toString().padStart(2, '0')}:${Math.floor(time % 60).toString().padStart(2, '0')}`;
const fallback = (r: any) => r?.parse_fallback || r?.exception_fallback || r?.emergency_fallback;
const pretty = (v: any) => JSON.stringify(v, null, 2);
const text = (m: any) => m?.content?.filter((c: any) => typeof c.text === 'string').map((c: any) => c.text).join('\n');

export function MatchWorkspace({ detail, selected, onSelect, connected, onRefresh }: {
  detail: any; selected: number; onSelect: (id: number) => void; connected: boolean; onRefresh: () => void;
}) {
  const [frames, setFrames] = useState<any[]>([]);
  const [frameIndex, setFrameIndex] = useState(0), [follow, setFollow] = useState(true);
  const [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1);
  const [round, setRound] = useState<number | null>(null), [tab, setTab] = useState('decision');
  const [commandIndex, setCommandIndex] = useState(0), [filter, setFilter] = useState('all');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [controlNotice, setControlNotice] = useState('');
  const cursor = useRef(-1), generation = useRef(0), currentRun = useRef<string | undefined>(undefined);
  const match = detail?.match, id = detail?.id;
  const running = detail?.status === 'RUNNING';

  useEffect(() => {
    const token = ++generation.current;
    if (currentRun.current !== id) {
      currentRun.current = id;
      cursor.current = -1; setFrames([]); setFrameIndex(0); setFollow(true); setPlaying(false); setRound(null);
      setError(''); setControlNotice('');
    }
    let fetching = false;
    async function load() {
      if (!id || fetching) return;
      fetching = true;
      try {
        for (let page = 0; page < 10; page++) {
          const result = await api(`/native/runs/${encodeURIComponent(id)}/frames?after=${cursor.current}&limit=500`);
          if (generation.current !== token) return;
          if (!result.frames.length) break;
          cursor.current = result.frames.at(-1).frame;
          setFrames(old => [...old, ...result.frames]);
          if (cursor.current >= result.last_frame) break;
        }
      } catch (e: any) { if (generation.current === token) setError(e.message); }
      finally { fetching = false; }
    }
    load();
    const timer = connected ? setInterval(load, 700) : undefined;
    return () => { generation.current++; clearInterval(timer); };
  }, [id, connected]);

  useEffect(() => {
    if (follow && frames.length) setFrameIndex(i => !running || frames.length - i > 20 ? frames.length - 1 : i);
  }, [frames.length, follow, running]);
  useEffect(() => {
    if (!follow || !running) return;
    // Consume actual 0.1-second physics frames instead of jumping seven frames
    // at each network refresh. Inference produces no frames and no fake motion.
    const timer = setInterval(() => setFrameIndex(i => Math.min(i + 1, Math.max(0, frames.length - 1))), 100);
    return () => clearInterval(timer);
  }, [follow, running, frames.length]);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setFrameIndex(i => {
      if (i >= frames.length - 1) { setPlaying(false); return i; }
      return i + 1;
    }), 100 / speed);
    return () => clearInterval(timer);
  }, [playing, speed, frames.length]);
  useEffect(() => { setCommandIndex(0); }, [selected, round]);

  const frame = frames[frameIndex];
  const state = frame?.state || match?.state;
  const tick = state?.tick ?? 0;
  const decisions = detail?.roles?.[roles[selected]]?.decisions || [];
  const atTick = (items: any[]) => round === null ? items.filter(r => r.tick <= tick).at(-1) : items.find(r => r.index === round);
  const row = atTick(decisions);
  const command = row?.commands?.[commandIndex] || row?.commands?.[0];
  const allRows = roles.flatMap((role, player) => (detail?.roles?.[role]?.decisions || []).map((r: any) => ({ ...r, player })));
  const visibleRows = allRows.filter(r => r.tick <= tick);
  const admissions = visibleRows.flatMap(r => r.admissions || []);
  const rejected = admissions.filter(a => !a.accepted).length;
  const flags = allRows.flatMap(r => (r.signals || []).map((s: any) => ({ ...s, row: r })))
    .filter(s => filter === 'all' || s.row.player === selected).sort((a, b) => a.row.tick - b.row.tick);
  const events = (match?.events || []).filter((e: any) => e.tick <= tick &&
    (filter === 'all' || (e.team_id === detail?.team && e.agent_id === selected)));
  const playerEvents = (match?.events || []).filter((e: any) => e.team_id === detail?.team && e.agent_id === selected);
  const eventCount = (name: string) => playerEvents.filter((e: any) => e.event === name).length;
  const playerAdmissions = decisions.flatMap((r: any) => r.admissions || []);
  const pending = match?.pending_roles || roles;
  const phase = match?.phase || 'starting';
  const time = state?.gameTime || 0;

  function seek(index: number) {
    setFollow(false); setPlaying(false); setRound(null);
    setFrameIndex(Math.max(0, Math.min(frames.length - 1, index)));
  }
  function inspect(player: number, item: any, after = false) {
    onSelect(player); setRound(item.index); setFollow(false); setPlaying(false); setTab('decision');
    const target = after && item.outcome ? item.outcome.end_tick : item.tick;
    const index = frames.findIndex(f => f.tick === target);
    if (index >= 0) setFrameIndex(index);
  }
  async function control(action: string) {
    if (!id) return;
    setBusy(true); setError('');
    try {
      const response = await post(`/native/runs/${encodeURIComponent(id)}/control`, { action });
      setControlNotice(action === 'stop' ? 'Stopping workers. Completed evidence will be retained.' : response.notice);
      onRefresh();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }
  function live() { setFollow(true); setPlaying(false); setRound(null); }
  const liveStatus = phase === 'deciding' ? `Thinking: ${5 - pending.length}/5 responses ready`
    : phase === 'advancing' ? 'Simulation advancing' : phase === 'paused' ? 'Paused at decision boundary'
      : phase === 'starting' ? 'Starting original agents' : detail?.status === 'COMPLETED' ? 'Full time' : phase;

  return <div className="match-workspace">
    <div className="match-heading"><div><div className="eyebrow"><span className="tag">AWS BASELINE</span><span>LOCAL 5v5 SIMULATION</span></div><h1>Watch the team. Understand the decisions.</h1><p>Original AWS agents, local Qwen, and a recorded link from observation to action to outcome.</p></div><span className={`native-run-state ${running ? 'is-running' : ''}`}>{detail?.status || 'READY'}</span></div>
    {!detail && <div className="match-welcome"><span className="welcome-number">5 v 5</span><h2>Your baseline, on the pitch.</h2><p>Start a match from the setup panel. Select an agent to follow its choices, rewind any decision, and inspect what the simulator actually did.</p><div className="welcome-steps"><span>01 &nbsp; Observe the game</span><span>02 &nbsp; Inspect a decision</span><span>03 &nbsp; Review the outcome</span></div></div>}
    {error && <div className="notice" role="alert">{error}<button className="text-button" aria-label="Dismiss match error" onClick={() => setError('')}>Dismiss</button></div>}
    {detail?.error && <div className="notice" role="alert">{detail.error}</div>}
    {detail && <>
      <div className="match-scorebar"><div className="score-side"><span className={`team-indicator ${detail.team === 0 ? 'own' : ''}`} /><div><b>Home</b><small>{detail.team === 0 ? 'AWS + Qwen' : match?.opponent || 'Scripted opponent'}</small></div></div><div className="match-score"><b>{state?.score?.home ?? 0}</b><span>:</span><b>{state?.score?.away ?? 0}</b><small>{clock(time)} <span>/ {clock(match?.duration)}</span></small></div><div className="score-side away"><div><b>Away</b><small>{detail.team === 1 ? 'AWS + Qwen' : match?.opponent || 'Scripted opponent'}</small></div><span className={`team-indicator ${detail.team === 1 ? 'own' : ''}`} /></div></div>
      <div className="match-session-bar"><span className={`phase-dot ${phase === 'deciding' && running ? 'thinking' : ''}`} /><span role="status">{liveStatus}</span><span className="session-note">{phase === 'deciding' && running ? 'Game clock holds while Qwen responds' : `${match?.interval || 2}s per decision interval`}</span><div className="match-controls"><button disabled={!running || busy || phase === 'paused'} onClick={() => control('pause')}>Pause match</button><button disabled={!running || busy || !match?.paused} onClick={() => control('resume')}>Resume</button><button disabled={!running || busy || phase !== 'paused'} onClick={() => control('step')}>Step one interval</button><button className="stop-match" disabled={!running || busy} onClick={() => control('stop')}>Stop</button></div></div>
      {controlNotice && <p className="control-notice" role="status">{controlNotice}</p>}
      <div className="match-metrics"><div><span>Decisions at this frame</span><b>{visibleRows.length}</b><small>Five independent native agents</small></div><div><span>Commands accepted</span><b>{admissions.length ? `${admissions.length - rejected} / ${admissions.length}` : '--'}</b><small>{rejected} rejected by local contract</small></div><div><span>Original fallbacks</span><b>{visibleRows.filter(fallback).length}</b><small>Separate from engine rejection</small></div><div><span>Observation to outcome</span><b>{number(match?.completed_rounds || 0)}<small> intervals</small></b><small>{match?.physics || 'Local physics v2'} · captured so far</small></div></div>

      <div className="match-grid"><section className="match-field-panel" aria-label="Match pitch and replay"><div className="panel-heading"><h2>{follow && running ? 'Live match view' : 'Reviewing recorded play'}</h2><span className={`view-badge ${follow ? 'live' : ''}`}>{follow && running ? 'LIVE' : 'REPLAY'} · {clock(time)}</span></div>
        <Pitch native={{ state, team: detail.team, command,
          label: follow && running ? 'LOCAL SIMULATOR / RECORDED POSITIONS' : 'RECORDED FRAME / NO MODEL CALLS',
          trail: round !== null && row?.outcome && tick >= row.outcome.end_tick ? [row.outcome.before.position, row.outcome.after.position] : undefined }} selected={selected} onSelect={onSelect} />
        <div className="match-replay"><div className="replay-labels"><span>{possession(state)}</span><span>Frame {frame?.frame ?? 0} / {frames.at(-1)?.frame ?? 0}</span></div><input aria-label="Match replay position" type="range" min="0" max={Math.max(0, frames.length - 1)} value={Math.min(frameIndex, Math.max(0, frames.length - 1))} onChange={e => seek(+e.target.value)} disabled={!frames.length} /><div className="match-replay-buttons"><button onClick={() => { if (frameIndex >= frames.length - 1) setFrameIndex(0); setFollow(false); setRound(null); setPlaying(!playing); }} disabled={frames.length < 2}>{playing ? 'Pause replay' : 'Play replay'}</button><button aria-label="Previous frame" onClick={() => seek(frameIndex - 1)} disabled={frameIndex === 0}>Back</button><button aria-label="Next frame" onClick={() => seek(frameIndex + 1)} disabled={frameIndex >= frames.length - 1}>Next</button><select aria-label="Replay speed" value={speed} onChange={e => setSpeed(+e.target.value)}>{[.5, 1, 2, 4].map(s => <option key={s} value={s}>{s}x replay</option>)}</select><button className={follow ? 'active' : ''} onClick={live}>Follow latest</button></div><p>Replay controls change your view. Match controls pause or stop execution. Dashed arrows show intent; dotted trails show observed displacement.</p></div>
      </section>

      <aside className="match-roster" aria-label="Five baseline agents"><div className="panel-heading"><h2>Your five agents</h2><span>{detail.team === 0 ? 'HOME' : 'AWAY'}</span></div>{roles.map((role, pid) => {
        const entries = detail.roles?.[role]?.decisions || [], current = atTick(entries);
        const admissions = current?.admissions || [], n = admissions.filter((a: any) => a.accepted).length;
        const observedPlayer = state?.players?.find((p: any) => p.teamCode === (detail.team === 0 ? 'HOME' : 'AWAY') && p.agentId === `agentId_${pid}`);
        return <button key={role} data-testid={`match-agent-${pid}`} className={`match-agent ${selected === pid ? 'selected' : ''}`} aria-pressed={selected === pid} onClick={() => onSelect(pid)}><span className="roster-number">{pid}<small>{codes[pid]}</small></span><span className="roster-info"><b>{names[pid]}</b><span>{current?.commands?.map((c: any) => c.commandType).join(' + ') || (running && pending.includes(role) ? 'Thinking...' : 'Awaiting decision')}</span><small>{current ? `${number(current.latency_ms, 0)} ms / ${n} accepted${fallback(current) ? ' / fallback' : ''}` : 'Original AWS handler'}</small><span className="stamina-track" title={`Stamina: ${number((observedPlayer?.stamina ?? 1) * 100, 0)}%`}><i style={{ width: `${(observedPlayer?.stamina ?? 1) * 100}%` }} /></span></span><span className="roster-status">{current?.signals?.length > 0 ? <span className="signal-count" title="Review cues in this decision">{current.signals.length}</span> : <span className="muted">Inspect</span>}{observedPlayer?.hasBall && <small>ON BALL</small>}</span></button>;
      })}<div className="roster-note">Cards describe the chosen frame or pinned decision. Select a player on the pitch or a role here.</div></aside></div>

      <section className="match-decision-panel" aria-label="Match decision inspector"><div className="panel-heading"><div><span className="label">DECISION INSPECTOR</span><h2><span className="agent-pill">{codes[selected]}</span>{names[selected]}<small>{row ? `Decision ${row.index + 1} / issued at ${clock(row.game_time)}` : 'Awaiting response'}</small></h2></div><div className="decision-picker"><label>Choose decision<select aria-label="Match inspected decision" value={row?.index ?? ''} onChange={e => { const r = decisions.find((d: any) => d.index === +e.target.value); if (r) inspect(selected, r); }} disabled={!decisions.length}>{!decisions.length && <option value="">Awaiting decision</option>}{decisions.map((r: any) => <option key={r.index} value={r.index}>#{r.index + 1} / {clock(r.game_time)}{r.signals?.length ? ` / ${r.signals.length} review cues` : ''}</option>)}</select></label>{row && <div className="decision-jumps"><button onClick={() => inspect(selected, row)}>View input</button><button disabled={!row.outcome} onClick={() => inspect(selected, row, true)}>View outcome</button></div>}</div></div>
        <div className="agent-performance" aria-label="Selected agent totals"><div><span>Captured decisions</span><b>{decisions.length}</b></div><div><span>Accepted commands</span><b>{playerAdmissions.filter((a: any) => a.accepted).length} / {playerAdmissions.length}</b></div><div><span>Original fallbacks</span><b>{decisions.filter(fallback).length}</b></div><div title="Direct simulator events; a pass in flight may remain unresolved"><span>Passes completed</span><b>{eventCount('pass_success')} / {eventCount('pass_attempt')}</b></div><div><span>Shots / saves</span><b>{eventCount('shot')} / {eventCount('save')}</b></div><div><span>Possession gains</span><b>{eventCount('possession_change')}</b></div><p>Selected agent totals across captured play, including later frames. No tactical score is inferred.</p></div>
        <div className="match-inspector-tabs" role="group" aria-label="Match evidence tabs">{[['decision', 'Decision & outcome'], ['state', 'Game state'], ['response', 'Model response'], ['prompt', 'Exact prompts'], ['logs', 'Handler logs']].map(([key, label]) => <button key={key} aria-pressed={tab === key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}</div>
        {!row ? <div className="match-empty">Waiting for this agent's first response. The match clock holds until all five are ready.</div> : tab === 'decision' ? <>
          <div className="evidence-flow"><div className="evidence-step"><span className="evidence-step-index">01 / OBSERVATION</span><h3>What the agent saw</h3><dl><dt>Game time</dt><dd>{clock(row.game_time)} / tick {row.tick}</dd><dt>Possession</dt><dd>{possession(row.observation)}</dd><dt>Position</dt><dd>{position(row.outcome?.before || findPlayer(row.observation, detail.team, selected))}</dd><dt>Stamina</dt><dd>{number((row.outcome?.stamina_before ?? findPlayer(row.observation, detail.team, selected)?.stamina) * 100, 0)}%</dd><dt>Score</dt><dd>{row.observation?.score?.home} : {row.observation?.score?.away}</dd></dl></div>
          <div className="evidence-step"><span className="evidence-step-index">02 / ACTION</span><h3>What it chose</h3>{row.commands?.length > 1 && <select aria-label="Match inspected command" value={commandIndex} onChange={e => setCommandIndex(+e.target.value)}>{row.commands.map((c: any, i: number) => <option key={i} value={i}>{i + 1}. {c.commandType}</option>)}</select>}<b className="readable-command">{command?.commandType || 'No commands returned'}</b><p>{describe(command)}</p><div className="decision-origin">{fallback(row) ? 'Original AWS fallback' : 'Original AWS parser output'}<span>{number(row.latency_ms, 0)} ms</span></div>{row.admissions?.length ? row.admissions.map((a: any, i: number) => <div key={i} className={`admission ${a.accepted ? 'accepted' : 'rejected'}`}><b>{a.accepted ? 'Accepted' : 'Rejected'}: {a.command.commandType}</b>{a.reasons?.length > 0 && <p>{a.reasons.join('; ')}</p>}</div>) : <p className="muted">Waiting for the remaining agents before submission.</p>}</div>
          <div className="evidence-step"><span className="evidence-step-index">03 / OBSERVED OUTCOME</span><h3>What happened next</h3>{row.outcome ? <><dl><dt>Window</dt><dd>{clock(row.game_time)} to {clock(row.outcome.end_time)}</dd><dt>Displacement</dt><dd>{number(row.outcome.distance_moved, 2)} units</dd><dt>Position after</dt><dd>{position(row.outcome.after)}</dd><dt>Stamina after</dt><dd>{number(row.outcome.stamina_after * 100, 0)}%</dd><dt>Ball before / after</dt><dd>{row.outcome.had_ball ? 'Held' : 'Not held'} / {row.outcome.has_ball ? 'Held' : 'Not held'}</dd></dl><div className="outcome-events">{row.events?.length ? row.events.map((e: any) => <span key={e.seq}>{e.event.replaceAll('_', ' ')} <small>{clock(e.simulation_time)}</small></span>) : <p>No actor-linked event recorded.</p>}</div></> : <p className="match-pending-outcome">{running ? 'Outcome pending: the decision interval has not finished.' : 'No completed outcome window was captured.'}</p>}<p className="causal-note">An observed change is not proof this action caused it. Event times may extend beyond this interval.</p></div></div>
          {row.signals?.length > 0 && <div className="decision-signals">{row.signals.map((s: any) => <div key={s.code} className={s.severity}><b>{s.title}</b><p>{s.explanation}</p></div>)}</div>}
          <details className="match-raw"><summary>Full native command array & submission evidence</summary><pre>{pretty({ commands: row.commands, admissions: row.admissions, history_before: row.conversation_messages_before, history_after: row.conversation_messages_after, observation_sha256: row.observation_sha256 })}</pre></details>
        </> : tab === 'state' ? <div className="match-code"><p>Exact observation supplied to this agent at {clock(row.game_time)}. All five agents in this round receive the same observation hash.</p><pre>{pretty(row.observation)}</pre></div>
          : tab === 'response' ? <div className="match-code"><p>Captured assistant output before native parsing and fallback.</p><pre>{text(row.last_assistant_message) || 'No assistant message captured.'}</pre></div>
            : tab === 'prompt' ? <div className="match-prompt-grid"><div><h3>Original system prompt</h3><pre>{detail.roles?.[roles[selected]]?.system_prompt || 'Unavailable'}</pre></div><div><h3>Actual state summary sent to the model</h3><pre>{text(row.last_user_message) || 'No user message captured.'}</pre><p>Private native conversation: {row.conversation_messages_before} to {row.conversation_messages_after} messages. History is retained within this match.</p></div></div>
              : <div className="match-code"><pre>{row.source_logs?.map((e: any) => `[${e.level}] ${e.message}`).join('\n') || 'No handler logs captured.'}</pre></div>}
      </section>

      <div className="match-review-grid"><section className="review-cues"><div className="panel-heading"><h2>Behavior to review <span className="count-pill">{flags.length}</span></h2><select aria-label="Evidence player filter" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All five agents</option><option value="selected">{names[selected]} only</option></select></div><p className="review-explainer">Explicit rule checks across captured decisions, including later frames. These are review cues, not an agent grade. Select a cue to inspect its input and outcome.</p>{flags.length ? <div className="review-list">{flags.map((s, i) => <button key={i} onClick={() => inspect(s.row.player, s.row)}><span className={`cue-marker ${s.severity}`} /><span><b>{s.title}</b><small>{names[s.row.player]} / decision {s.row.index + 1} / {clock(s.row.game_time)}</small></span><span>Inspect</span></button>)}</div> : <div className="match-empty">No rules have flagged a captured decision. This does not establish good tactics.</div>}</section>
      <section className="match-events"><div className="panel-heading"><h2>Game events</h2><span>Through {clock(time)}</span></div><p className="review-explainer">Recorded simulator events. Linked events reopen the corresponding action; team events carry no invented individual credit.</p><div className="event-list">{events.length ? events.slice(-80).reverse().map((e: any) => {
        const linked = allRows.find(r => r.decision_id === e.decision_id);
        return <button key={e.seq} disabled={!linked} onClick={() => linked && inspect(linked.player, linked)}><time>{clock(e.simulation_time)}</time><span><b>{e.event.replaceAll('_', ' ')}</b><small>{e.team_id === 0 ? 'Home' : e.team_id === 1 ? 'Away' : 'Match'}{e.agent_id !== undefined ? ` / player ${e.agent_id}` : ''}{e.reason ? ` / ${e.reason}` : ''}</small></span><span>{linked ? 'Inspect' : ''}</span></button>;
      }) : <div className="match-empty">No events for this selection yet.</div>}</div></section></div>
      <section className="match-decision-history"><div className="panel-heading"><h2>Decision history</h2><span>{names[selected]} / {decisions.length} captured decisions</span></div><div className="history-table"><table><thead><tr><th>Game time</th><th>Command</th><th>Local acceptance</th><th>Response time</th><th>Review cues</th></tr></thead><tbody>{decisions.map((r: any) => <tr key={r.index} className={row?.index === r.index ? 'selected' : ''}><td><button className="history-link" onClick={() => inspect(selected, r)}>#{r.index + 1} / {clock(r.game_time)}</button></td><td>{r.commands?.map((c: any) => c.commandType).join(' + ')}</td><td>{r.admissions?.length ? `${r.admissions.filter((a: any) => a.accepted).length} / ${r.admissions.length}` : 'Pending'}</td><td>{number(r.latency_ms, 0)} ms</td><td>{r.signals?.length || 0}</td></tr>)}</tbody></table>{!decisions.length && <div className="match-empty">Decisions appear as each native handler returns.</div>}</div></section>
    </>}
    <div className="match-footnote"><b>Baseline preserved. Environment made explicit.</b><p>The original AWS agents control your five players against a labeled scripted opponent in simplified local physics. Full command arrays are retained; the engine applies its existing one-valid-command-per-player rule, and rejections stay visible. Inference pauses simulated time. Results describe this local environment, not the AWS competition engine.</p></div>
  </div>;
}

function findPlayer(state: any, team: number, pid: number) { return state?.players?.find((p: any) => p.teamCode === (team === 0 ? 'HOME' : 'AWAY') && p.agentId === `agentId_${pid}`); }
function position(p: any) { return p?.position ? `(${number(p.position.x, 1)}, ${number(p.position.y, 1)})` : '--'; }
function describe(c: any) {
  const p = c?.parameters || {};
  switch (c?.commandType) {
    case 'MOVE_TO': return `Move to (${number(p.target_x, 1)}, ${number(p.target_y, 1)})${p.sprint ? ' at a sprint.' : '.'}`;
    case 'PASS': return `Pass to teammate ${p.target_player_id} using ${p.type?.toLowerCase()} delivery.`;
    case 'SHOOT': return `Shoot toward ${p.aim_location}, with power ${p.power}.`;
    case 'GK_DISTRIBUTE': return `Distribute to teammate ${p.target_player_id} with a ${p.method?.toLowerCase()}.`;
    case 'PRESS_BALL': return `Pursue the ball with intensity ${p.intensity}.`;
    case 'INTERCEPT': return p.aggressive ? 'Attempt an aggressive interception.' : 'Move to intercept the ball.';
    case 'MARK': return `Mark opponent ${p.target_player_id} with ${p.tightness?.toLowerCase()} coverage.`;
    case 'FOLLOW_PLAYER': return `Follow ${p.target_team} player ${p.target_player_id} at distance ${p.distance}.`;
    case 'SET_STANCE': return `Set the ${['balanced', 'attacking', 'defensive'][p.stance] || p.stance} stance. Local physics records stance without a separate AI controller.`;
    case 'CLEAR_OVERRIDE': return 'Clear this player intent. The local engine holds position.';
    default: return 'Inspect the exact native command and local admission result below.';
  }
}
