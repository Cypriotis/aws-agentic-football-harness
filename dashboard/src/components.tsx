export function Section({index,title,right}:{index:string;title:string;right?:React.ReactNode}) {return <div className="section-title"><span className="section-index">{index}</span><h2>{title}</h2><div className="section-right">{right}</div></div>}
export function Empty({children}:{children:React.ReactNode}) {return <div className="empty">{children}</div>}
