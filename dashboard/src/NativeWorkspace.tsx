import { useEffect, useRef, useState } from 'react';
import { api, number, post } from './api';
import { Empty, Section } from './components';
import { Pitch } from './Pitch';
import { MatchWorkspace } from './MatchWorkspace';

const roles = ['goalkeeper', 'defender', 'midfielder', 'forward1', 'forward2'];
const labels = ['GOALKEEPER', 'DEFENDER', 'MIDFIELDER', 'FORWARD 01', 'FORWARD 02'];
const roleNames = ['Goalkeeper', 'Defender', 'Midfielder', 'Forward 1', 'Forward 2'];
const original = ['NOVA MICRO', 'NOVA LITE', 'NOVA PRO', 'NOVA MICRO', 'NOVA LITE'];
type Catalog = { health: { model: string; endpoint: string; models: string[]; ready: boolean; error?: string; dependencies: Record<string, string> }; runs: any[]; source_commit: string; active: boolean };
const fallback = (r: any) => r?.parse_fallback || r?.exception_fallback || r?.emergency_fallback;
const pretty = (value: any) => JSON.stringify(value, null, 2);

export function NativeWorkspace({ connected }: { connected: boolean }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [runId, setRunId] = useState(new URLSearchParams(location.search).get('native') || localStorage.getItem('native-selected-run') || '');
  const [detail, setDetail] = useState<any>(null);
  const [model, setModel] = useState('');
  const [team, setTeam] = useState(0), [repeats, setRepeats] = useState(1);
  const [input, setInput] = useState('sample'), [custom, setCustom] = useState('');
  const [selected, setSelected] = useState(2), [index, setIndex] = useState<number | null>(null);
  const [inspector, setInspector] = useState('COMMANDS');
  const [commandIndex, setCommandIndex] = useState(0);
  const [setupOpen, setSetupOpen] = useState(false);
  const [kind, setKind] = useState('match');
  const [duration, setDuration] = useState(30), [interval, setIntervalSeconds] = useState(2);
  const [seed, setSeed] = useState(42), [opponent, setOpponent] = useState('AggressiveBaseline');
  const [error, setError] = useState(''), [launching, setLaunching] = useState(false);
  const currentId = useRef(runId), refreshing = useRef(false), initialized = useRef(false);
  const alive = useRef(true);

  function selectRun(id: string) {
    currentId.current = id; setRunId(id); setIndex(null); setCommandIndex(0); setDetail(null);
    localStorage.setItem('native-selected-run', id);
    const url = new URL(location.href); url.searchParams.set('native', id); history.replaceState({}, '', url);
  }

  async function refresh() {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const data = await api<Catalog>('/native');
      if (!alive.current) return;
      setCatalog(data);
      setModel(value => value || (data.health.models.includes(data.health.model) ? data.health.model : data.health.models[0] || data.health.model));
      if (!initialized.current) {
        initialized.current = true;
        if (!data.runs.some(r => r.id === currentId.current)) selectRun(data.runs[0]?.id || '');
      }
      const id = currentId.current;
      if (id) {
        const value = await api(`/native/runs/${encodeURIComponent(id)}`);
        if (alive.current && currentId.current === id) setDetail(value);
      }
    } catch (e: any) { if (alive.current) setError(e.message); }
    finally { refreshing.current = false; }
  }

  useEffect(() => {
    alive.current = true; refresh();
    const timer = connected ? setInterval(refresh, 1000) : undefined;
    return () => { alive.current = false; clearInterval(timer); };
  }, [connected]);

  useEffect(() => {
    if (!runId) return;
    let active = true;
    api(`/native/runs/${encodeURIComponent(runId)}`).then(value => {
      if (active && currentId.current === runId) setDetail(value);
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [runId]);

  async function launch(e: React.FormEvent) {
    e.preventDefault(); setError(''); setLaunching(true);
    try {
      let state = undefined;
      if (kind === 'snapshot' && input === 'custom') {
        state = JSON.parse(custom);
        if (!state || typeof state !== 'object' || Array.isArray(state) || state.prompt || state.gameState)
          throw new Error('Paste a raw game state object, without a prompt or gameState envelope.');
      }
      const result = await post('/native/runs', { model, team, repeats, state, kind, duration, interval, seed, opponent });
      selectRun(result.id); setSetupOpen(false); await refresh();
    } catch (e: any) { setError(e.message); }
    finally { setLaunching(false); }
  }

  const own = detail?.roles?.[roles[selected]];
  const decisions = own?.decisions || [];
  const row = decisions[index ?? decisions.length - 1];
  const totals = detail?.totals;
  const running = detail?.status === 'RUNNING';
  const ledger = roles.flatMap((role, player) => (detail?.roles?.[role]?.decisions || []).map((value: any, i: number) => ({ ...value, player, repeat: i })))
    .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
  const assistant = row?.last_assistant_message?.content?.filter((c: any) => typeof c.text === 'string').map((c: any) => c.text).join('\n');

  const activeCommand = row?.commands?.[commandIndex] || row?.commands?.[0];
  const maxLatency = Math.max(1000, ...ledger.map(r => r.latency_ms || 0));
  useEffect(() => { setCommandIndex(0); }, [selected, index, row?.index]);

  async function exportReport() {
    try {
    const report = await api(`/native/runs/${encodeURIComponent(runId)}/report`);
    const url = URL.createObjectURL(new Blob([pretty(report)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = `${runId}.json`; a.click(); URL.revokeObjectURL(url);
    } catch (e: any) { setError(e.message); }
  }

  return <div className="native-workspace">
    <aside className={`native-controls ${setupOpen ? 'setup-open' : ''}`} aria-label="AWS baseline configuration">
      <div className="sidebar-kicker"><span>WORKSPACE</span><span>01</span></div>
      <div className="sidebar-title">Baseline<br /><em>match lab.</em></div><button className="button outline setup-toggle" aria-expanded={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>{setupOpen ? 'Close setup' : 'Configure evaluation'}</button>
      <div className="native-ready"><span className={`connection-dot ${catalog?.health.ready ? '' : 'disconnected'}`} />{catalog?.health.ready ? 'QWEN AVAILABLE' : catalog ? 'RUNTIME UNAVAILABLE' : 'CHECKING LOCAL RUNTIME'}</div>
      {catalog?.health.error && <p className="native-warning" role="status">{catalog.health.error}. Start Ollama and install the project dependencies.</p>}
      <form onSubmit={launch} className="native-launch">
        <span className="label native-next-label">Start a baseline run</span>
        <label>Run type<select aria-label="Native run type" value={kind} onChange={e => setKind(e.target.value)}><option value="match">5v5 simulated match</option><option value="snapshot">Fixed observation test</option></select></label>
        <label>Qwen model<select aria-label="Native local model" value={model} onChange={e => setModel(e.target.value)} disabled={!catalog?.health.models.length}>
          {!catalog?.health.models.length && <option value={model}>{model || 'CHECKING…'}</option>}
          {catalog?.health.models.map(name => <option key={name}>{name}</option>)}
        </select></label>
        <div className="native-form-pair"><label>Your team<select aria-label="Native controlled side" value={team} onChange={e => setTeam(+e.target.value)}><option value="0">Home</option><option value="1">Away</option></select></label>
          {kind === 'snapshot' ? <label>Repeats per agent<select aria-label="Native repetitions" value={repeats} onChange={e => setRepeats(+e.target.value)}>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label> : <label>Game length<select aria-label="Match duration" value={duration} onChange={e => setDuration(+e.target.value)}>{[6, 12, 30, 60, 120].map(n => <option key={n} value={n}>{n} seconds</option>)}</select></label>}</div>
        {kind === 'match' ? <><label>Scripted opponent<select aria-label="Match opponent" value={opponent} onChange={e => setOpponent(e.target.value)}><option value="AggressiveBaseline">Aggressive baseline</option><option value="DefensiveBaseline">Defensive baseline</option><option value="RandomBaseline">Random baseline</option></select></label><div className="native-form-pair"><label>Decision interval<select aria-label="Match decision interval" value={interval} onChange={e => setIntervalSeconds(+e.target.value)}>{[1,2,3].map(n => <option key={n} value={n}>{n} seconds</option>)}</select></label><label>World seed<input aria-label="Match seed" type="number" min="0" max="2147483647" required value={seed} onChange={e => setSeed(+e.target.value)} /></label></div><p className="match-budget">{duration / interval} intervals / {duration / interval * 5} native decisions. Game time pauses while Qwen thinks; wall-clock duration will be longer.</p></> : <><label>Observation<select aria-label="Native input observation" value={input} onChange={e => setInput(e.target.value)}><option value="sample">AWS sample state</option><option value="custom">Custom state JSON</option></select></label>{input === 'custom' && <label>RAW GAME STATE<textarea aria-label="Native custom game state" value={custom} onChange={e => setCustom(e.target.value)} placeholder={'{"tick": 0, "ball": {...}, "players": [...] }'} spellCheck={false} maxLength={100000} /></label>}</>}
        <button className="button full" type="submit" disabled={launching || catalog?.active || !catalog?.health.ready}>{launching ? 'Starting...' : catalog?.active ? 'Agents running...' : kind === 'match' ? 'Start 5v5 match' : 'Run five agents'}</button>
        <p className="small muted">{kind === 'match' ? 'Your five players use unchanged AWS handlers with private native history. The opponent uses a labeled scripted policy.' : 'All five original agents receive this observation. Repeats retain private conversation history.'}</p>
      </form>
      <div className="native-source"><span className="label">SOURCE FIDELITY</span><b>AWS BALANCED / PINNED</b><span>{catalog?.source_commit.slice(0, 12) || 'VERIFYING…'}</span><p>Original prompts, parser and fallback.<br />One model-provider import replaced.</p><a href="https://github.com/aws-samples/sample-ai-possibilities/tree/02fadb4bc7748eeb0d2ab6db150edfbd5766f63f/agentic-football-sample-agents/ai-team-strands-balanced" target="_blank" rel="noreferrer">[ VIEW AWS REFERENCE ↗ ]</a></div>
      <div className="native-history"><span className="label">Saved evaluations</span><select aria-label="Native evaluation history" value={runId} onChange={e => { selectRun(e.target.value); refresh(); }}><option value="">Choose an evaluation</option>{catalog?.runs.map(run => <option value={run.id} key={run.id}>{new Date(run.created_at || Date.UTC(+run.id.slice(18,22), +run.id.slice(22,24)-1, +run.id.slice(24,26), +run.id.slice(27,29), +run.id.slice(29,31))).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} / {run.status} / {run.id.slice(-8)}</option>)}</select><button className="button outline full" onClick={exportReport} disabled={!detail}>Export evaluation</button></div>
    </aside>

    <div className="native-main">{detail?.kind === 'match' || (!detail && kind === 'match') ? <><MatchWorkspace detail={detail} selected={selected} onSelect={setSelected} connected={connected} onRefresh={refresh} />{error && <div className="notice" role="alert"><span>{error}</span><button className="text-button" onClick={() => setError('')}>Dismiss</button></div>}</> : <>
      <div className="page-heading native-heading"><div><div className="eyebrow"><span className="tag">AWS BASELINE</span><span>Five agents. One observation.</span></div><h1>Understand every decision.</h1><p>Inspect what your agents see, choose, and return before you start hardening.</p></div><span className={`native-run-state ${running ? 'is-running' : ''}`} role="status">{running && <span className="connection-dot" />}{detail?.status || 'READY'}</span></div>
      <div className="native-boundary"><span className="observation-icon" aria-hidden="true">01</span><div><b>Observation test</b><p>This pitch is a fixed input state. Select a player to inspect its decision and intended target.</p></div><span className="tag">No match running</span></div>
      {error && <div className="notice" role="alert"><span>{error}</span><button className="text-button" onClick={() => setError('')} aria-label="Dismiss native error">[ × ]</button></div>}
      {detail?.error && <div className="notice" role="alert">{detail.error}</div>}
      <div className="run-strip"><span>MODEL <b>{detail?.model || model || '—'}</b></span><span>SIDE <b>{(detail?.team ?? team) === 0 ? 'HOME' : 'AWAY'}</b></span><span>RECORD <b>{runId ? runId.slice(-8) : 'NOT STARTED'}</b></span><span>{connected ? '[ AUTO REFRESH / 1 s ]' : '[ VIEW PAUSED / EXECUTION CONTINUES ]'}</span></div>
      <div className="metrics-row native-metrics">
        <div className="metric-pair"><span className="label">Decisions captured</span><div className="metric-value">{totals ? totals.decisions : '—'}<small>{totals ? ` / ${totals.expected}` : ''}</small></div><span className="metric-note">Across five independent agents</span></div>
        <div className="metric-pair"><span className="label">Responses without fallback</span><div className="metric-value">{totals?.decisions ? number((totals.decisions - totals.fallbacks) / totals.decisions * 100, 1) : '—'}<small>{totals?.decisions ? '%' : ''}</small></div><span className="metric-note">{totals ? `${totals.fallbacks} original fallback(s)` : 'Awaiting native responses'}</span></div>
        <div className="metric-pair"><span className="label">Response time / p95</span><div className="metric-value">{number(totals?.p95, 0)}<small>ms</small></div><span className="metric-note">Includes local model queueing</span></div>
        <div className="metric-pair"><span className="label">Under 1 second</span><div className="metric-value">{totals ? totals.under_1000 : '—'}<small>{totals ? ` / ${totals.decisions}` : ''}</small></div><span className="metric-note">500 ms: {totals ? `${totals.under_500} / ${totals.decisions}` : 'unavailable'} · diagnostic</span></div>
      </div>
      <Section index="01" title="The starting five" right={<span className="label">Select an agent to inspect</span>} />
      <div className="agent-grid native-agents">{roles.map((role, player) => {
        const data = detail?.roles?.[role], items = data?.decisions || [], latest = items.at(-1);
        const status = items.length >= (detail?.repeats || 1) ? 'Complete' : running ? (items.length ? 'Thinking' : 'Waiting') : data?.error ? 'Failed' : detail ? detail.status : 'Ready';
        return <button className={`agent-card native-agent ${selected === player ? 'selected' : ''}`} key={role} data-testid={`native-agent-${player}`} aria-pressed={selected === player} onClick={() => { setSelected(player); setIndex(null); }}>
          <div className="agent-top"><span className="agent-number">0{player}</span><span className={`status-text ${fallback(latest) ? 'warning' : ''}`}>{status}</span></div>
          <div className="native-role-name">{roleNames[player]}</div><div className="agent-action">{latest?.commands?.map((c: any) => c.commandType).join(' + ') || (running ? 'Awaiting response' : 'No decision yet')}</div>
          <div className="agent-meta"><span>{number(latest?.latency_ms, 0)} <small>ms</small></span><span>{items.length} / {detail?.repeats || repeats}</span></div>
          <div className="agent-bottom"><span>{latest ? fallback(latest) ? 'Fallback used' : 'Model response' : 'Original AWS agent'}</span><span>Inspect</span></div>
        </button>;
      })}</div>

      <div className="native-board">
        <div className="native-observation"><Section index="02" title="Field observation" right={<span className="label">Dashed line = intended action</span>} />
          <Pitch native={{ state: detail?.display_state, team: detail?.team ?? team, command: activeCommand }} selected={selected} onSelect={player => { setSelected(player); setIndex(null); }} />
          <details className="native-input"><summary>View original input JSON</summary><pre>{detail?.state ? pretty(detail.state) : 'Start an evaluation or open an existing record.'}</pre></details>
        </div>
        <aside className="inspector native-inspector" aria-label="Native agent inspector">
          <div className="inspector-heading"><span className="label">DECISION INSPECTOR</span></div><h3>0{selected}<span>{roleNames[selected]}</span></h3>
          <div className="native-inspector-spec"><span>ORIGINAL MODEL</span><b>{original[selected]}</b><span>LOCAL REPLACEMENT</span><b>{detail?.model || model || '—'}</b><span>NATIVE HISTORY</span><b>{row ? `${row.conversation_messages_before} → ${row.conversation_messages_after} messages` : 'NO RECORD'}</b></div>
          <label className="native-repeat-picker">Decision<select aria-label="Native inspected decision" value={index ?? Math.max(0, decisions.length - 1)} onChange={e => setIndex(+e.target.value)} disabled={!decisions.length}>{!decisions.length && <option value="0">AWAITING OUTPUT</option>}{decisions.map((r: any, i: number) => <option key={i} value={i}>0{i + 1} / {number(r.latency_ms, 0)} ms{fallback(r) ? ' / FALLBACK' : ''}</option>)}</select></label>
          <div className="native-inspector-tabs" role="group" aria-label="Native evidence tabs">{['COMMANDS', 'RESPONSE', 'PROMPT', 'LOG'].map(tab => <button key={tab} className={`text-button ${inspector === tab ? 'active' : ''}`} aria-pressed={inspector === tab} onClick={() => setInspector(tab)}>{{ COMMANDS: 'Actions', RESPONSE: 'Response', PROMPT: 'Prompt', LOG: 'Logs' }[tab]}</button>)}</div>
          {inspector === 'COMMANDS' ? row ? <><ActionSummary command={activeCommand} fallback={fallback(row)} />{row.commands?.length > 1 && <label className="native-repeat-picker">Action shown on pitch<select aria-label="Inspected command" value={commandIndex} onChange={e => setCommandIndex(+e.target.value)}>{row.commands.map((c: any, i: number) => <option key={i} value={i}>{i + 1}. {c.commandType}</option>)}</select></label>}<span className={`tag ${fallback(row) ? 'warning' : ''}`}>{fallback(row) ? 'ORIGINAL AWS FALLBACK' : 'ORIGINAL PARSER OUTPUT'}</span><pre>{pretty(row.commands)}</pre><p className="small muted">Complete returned array. No local repair or semantic validator was applied after the AWS handler.</p></> : <Empty>{running ? 'Waiting for this agent’s first completed decision.' : 'Run the agents to capture commands.'}</Empty>
            : inspector === 'RESPONSE' ? <pre>{assistant || 'No assistant response captured for this decision.'}</pre>
              : inspector === 'PROMPT' ? <><pre>{own?.system_prompt || 'The prompt is captured when a native worker starts. Older recordings retain its SHA256 only.'}</pre><div className="native-hash">SHA256 / {own?.system_prompt_sha256 || 'UNAVAILABLE'}</div></>
                : <pre>{row?.source_logs?.map((log: any) => `[${log.level}] ${log.message}`).join('\n') || 'No native handler log captured yet.'}</pre>}
        </aside>
      </div>
      <Section index="03" title="Response time by agent" right={<span className="label">Wall-clock time / milliseconds</span>} />
      <div className="native-latency-panel">{roles.map((role, player) => {
        const values = detail?.roles?.[role]?.decisions || [];
        return <button key={role} className={`native-latency-row ${selected === player ? 'selected' : ''}`} onClick={() => { setSelected(player); setIndex(null); }}><span>{roleNames[player]}</span><div className="native-latency-track">{values.length ? values.map((r: any, i: number) => <span key={i} className={fallback(r) ? 'has-fallback' : ''} style={{ width: `${Math.max(1, r.latency_ms / maxLatency * 100)}%` }} title={`Decision ${i + 1}: ${number(r.latency_ms, 0)} ms${fallback(r) ? ' / fallback' : ''}`} />) : <span className="no-latency" />}</div><b>{values.length ? `${number(values.at(-1).latency_ms, 0)} ms` : 'Awaiting data'}</b></button>;
      })}<p>Each bar is a recorded decision. Timing includes model queueing; amber marks a fallback. Match outcomes are unavailable in this workspace.</p></div>
      <Section index="04" title="Decision timeline" right={<span className="label">In completion order</span>} />
      <div className="native-ledger">{!ledger.length ? <Empty>{running ? 'Execution continues independently of this browser. Completed decisions will appear here.' : 'No decisions recorded. Run five agents to begin.'}</Empty> : ledger.map((item, i) => <button key={`${item.player}-${item.repeat}`} className={`native-decision-row ${selected === item.player && (index ?? decisions.length - 1) === item.repeat ? 'selected' : ''}`} onClick={() => { setSelected(item.player); setIndex(item.repeat); }}><span className="muted">{String(i + 1).padStart(2, '0')}</span><b>0{item.player} / {roleNames[item.player]}</b><span>{item.commands?.map((c: any) => c.commandType).join(' + ') || 'EMPTY'}</span><span>{number(item.latency_ms, 0)} ms</span><span className={fallback(item) ? 'native-warning' : ''}>{fallback(item) ? 'Fallback' : 'Model response'} ↗</span></button>)}</div>
      <div className="native-evidence-note"><span className="label">INTERPRETATION</span><p>This workspace runs the original AWS balanced handlers with local Qwen. Responses, fallbacks and timing are observed directly. Football performance requires an evolving match environment.</p></div>
    </>}</div>
  </div>;
}

function ActionSummary({ command, fallback }: { command: any; fallback: boolean }) {
  if (!command) return null;
  const p = command.parameters || {};
  const descriptions: Record<string, string> = {
    MOVE_TO: `Move to (${p.target_x}, ${p.target_y})${p.sprint ? ' with sprint' : ''}`,
    PASS: `Pass to teammate ${p.target_player_id} / ${p.type || 'ground'}`,
    GK_DISTRIBUTE: `Distribute to teammate ${p.target_player_id} / ${p.method || ''}`,
    SHOOT: `Shoot toward ${p.aim_location} / power ${p.power}`,
    MARK: `Mark opponent ${p.target_player_id} / ${p.tightness || ''}`,
    PRESS_BALL: `Press the ball / intensity ${p.intensity}`,
    INTERCEPT: p.aggressive ? 'Attempt an aggressive interception' : 'Move to intercept the ball',
    FOLLOW_PLAYER: `Follow ${p.target_team} player ${p.target_player_id} / distance ${p.distance}`,
    SET_STANCE: `Set stance to ${['balanced', 'attack', 'defend'][p.stance] || p.stance}`,
    CLEAR_OVERRIDE: 'Return control to default AI',
    RESET: 'Clear team overrides',
  };
  return <div className={`native-action-summary ${fallback ? 'has-fallback' : ''}`}><span className="label">{fallback ? 'Fallback action' : 'Selected action'}</span><strong>{command.commandType}</strong><p>{descriptions[command.commandType] || 'Inspect the returned parameters below.'}</p></div>;
}
