import { number } from './api';

const teamOf=(p:any)=>p.teamId??(String(p.teamCode).toLowerCase()==='away'?1:0);
const idOf=(p:any)=>p.playerId??Number(String(p.agentId).replace('agentId_',''));
const x=(value:number)=>50+(value+55)/110*1000;
const y=(value:number)=>35+(value+35)/70*630;
export function possession(state:any):string {
  if(!state?.ball)return 'UNAVAILABLE';
  if(state.ball.isFree)return 'LOOSE BALL';
  const holder=state.players?.find((p:any)=>p.hasBall===true);
  if(holder)return `${teamOf(holder)===0?'HOME':'AWAY'} / ${idOf(holder)}`;
  return 'UNKNOWN HOLDER';
}
export function Pitch({selected,onSelect,native}:{selected:number;onSelect:(id:number)=>void;native:{state:any;team:number;command?:any;label?:string;trail?:{x:number;y:number}[]}}) {
  const state=native.state, players=state?.players||[], team=native.team;
  const actor=players.find((p:any)=>teamOf(p)===team&&idOf(p)===selected);
  const command=native.command;
  const params=command?.parameters||{};
  let target:any=null;
  if(command?.commandType==='MOVE_TO')target={x:params.target_x,y:params.target_y};
  if(['PRESS_BALL','INTERCEPT','SLIDE_TACKLE'].includes(command?.commandType))target=state?.ball?.position;
  if(command?.commandType==='SHOOT')target={x:team===0?55:-55,y:params.aim_location==='CENTER'?0:['TR','BR'].includes(params.aim_location)?2.6:-2.6};
  if(['PASS','GK_DISTRIBUTE','MARK','FOLLOW_PLAYER'].includes(command?.commandType)) {
    const targetTeam=['PASS','GK_DISTRIBUTE'].includes(command.commandType)?team:command.commandType==='FOLLOW_PLAYER'?(params.target_team==='HOME'?0:1):1-team;
    target=players.find((p:any)=>teamOf(p)===targetTeam&&idOf(p)===params.target_player_id)?.position;
  }
  return <div className="pitch-wrap">
    <div className="pitch-caption"><span>{native.label || 'INPUT SNAPSHOT / NO MATCH SIMULATION'}</span><span>X ±55 · Y ±35</span></div>
    <svg className="pitch" viewBox="0 0 1100 700" role="img" aria-label={state?'Recorded football positions and intended action target':'Pitch awaiting observed match positions'}>
      <defs><pattern id="pitchgrid" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M 50 0 L 0 0 0 50" fill="none" stroke="var(--rule)" strokeWidth=".35"/></pattern><marker id="target-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="var(--accent)" strokeWidth="1.2"/></marker></defs>
      <rect x="50" y="35" width="1000" height="630" fill="url(#pitchgrid)"/>
      <g fill="none" stroke="var(--pitch-line)" strokeWidth="1.4"><rect x="50" y="35" width="1000" height="630"/><path d="M550 35V665"/><circle cx="550" cy="350" r="92"/><circle cx="550" cy="350" r="3" fill="var(--pitch-line)"/><rect x="50" y="155" width="165" height="390"/><rect x="885" y="155" width="165" height="390"/><rect x="50" y="257" width="60" height="186"/><rect x="990" y="257" width="60" height="186"/><path d="M50 317H28V383H50M1050 317H1072V383H1050"/><circle cx="160" cy="350" r="2"/><circle cx="940" cy="350" r="2"/></g>
      <g className="pitch-axis"><text x="50" y="21">−55</text><text x="540" y="21">0</text><text x="1023" y="21">+55</text><text x="50" y="688">HOME / DEFENDS −X</text><text x="856" y="688">AWAY / DEFENDS +X</text></g>
      {native?.trail?.length===2&&<g className="observed-trail"><line x1={x(native.trail[0].x)} y1={y(native.trail[0].y)} x2={x(native.trail[1].x)} y2={y(native.trail[1].y)} stroke="var(--signal-ok)" strokeWidth="4" strokeDasharray="2 5"/><circle cx={x(native.trail[0].x)} cy={y(native.trail[0].y)} r="13" fill="none" stroke="var(--signal-ok)" strokeWidth="2"/><title>Observed displacement during selected interval</title></g>}
      {actor&&target&&Number.isFinite(target.x)&&Number.isFinite(target.y)&&<g><line x1={x(actor.position.x)} y1={y(actor.position.y)} x2={x(target.x)} y2={y(target.y)} stroke="var(--accent)" strokeWidth="2" strokeDasharray="7 5" markerEnd="url(#target-arrow)"/><path d={`M${x(target.x)-7} ${y(target.y)}h14 M${x(target.x)} ${y(target.y)-7}v14`} stroke="var(--accent)"/><text x={Math.min(930,x(target.x)+12)} y={Math.max(60,y(target.y)-12)} className="target-label">{command.commandType} / INTENDED</text></g>}
      {players.map((p:any)=>{const own=teamOf(p)===team,id=idOf(p),selectedPlayer=own&&id===selected;return <g key={`${teamOf(p)}-${id}`} transform={`translate(${x(p.position.x)},${y(p.position.y)})`} className={own?'pitch-player own':'pitch-player opponent'} tabIndex={own?0:undefined} role={own?'button':undefined} aria-label={`${own?'Select ':''}${teamOf(p)===0?'HOME':'AWAY'} player ${id}`} onClick={()=>own&&onSelect(id)} onKeyDown={e=>{if(own&&['Enter',' '].includes(e.key)){e.preventDefault();onSelect(id)}}}>
        <title>{`${teamOf(p)===0?'HOME':'AWAY'} ${id} · (${number(p.position.x,1)}, ${number(p.position.y,1)}) · stamina ${number(p.stamina*100)}%`}</title>
        <circle r="23" fill="transparent" stroke="none"/>{selectedPlayer&&<><circle r="24" fill="none" stroke="var(--accent)" strokeDasharray="3 3"/><path d="M-30 0h7M23 0h7M0-30v7M0 23v7" stroke="var(--accent)"/></>}
        <circle r="18" fill={own?'var(--accent)':'var(--paper)'} stroke={own?'var(--accent)':'var(--ink)'} strokeWidth="1.8"/><text textAnchor="middle" dy="6" fill={own?'var(--paper)':'var(--ink)'} fontSize="20" fontWeight="500">{id}</text><text textAnchor="middle" y="-29" className="player-label">{teamOf(p)===0?'H':'A'}{id}</text>
      </g>})}
      {state?.ball?.position&&<g transform={`translate(${x(state.ball.position.x)},${y(state.ball.position.y)})`}><circle r="9" fill="var(--paper)" stroke="var(--ink)"/><circle r="3.5" fill="var(--ink)"/><title>Observed ball position</title></g>}
      {!state&&<g><rect x="330" y="315" width="440" height="70" fill="var(--paper)"/><text x="550" y="355" textAnchor="middle" className="empty-pitch">&gt; AWAITING OBSERVED STATE</text></g>}
    </svg>
    <div className="pitch-footer"><span><i className="legend-dot"/> CONTROLLED TEAM {team===0?'HOME':'AWAY'}</span><span><i className="legend-dot outline"/> OPPONENT</span><span className="pitch-possession">{possession(state)}</span></div>
  </div>;
}
