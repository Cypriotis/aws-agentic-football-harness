import { useEffect, useState } from 'react';
import { NativeWorkspace } from './NativeWorkspace';

export default function App() {
  const [connected, setConnected] = useState(true);
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('theme', theme);
  }, [theme]);
  useEffect(() => {
    if (location.pathname !== '/' && location.pathname !== '/native')
      history.replaceState({}, '', '/native' + location.search);
  }, []);
  return <div className="app-shell baseline-app">
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <header className="topbar baseline-topbar">
      <a href="/native" className="wordmark"><span className="baseline-mark" aria-hidden="true">f/</span><span>Football Lab<small>Agent observatory</small></span></a>
      <div className="baseline-profile"><span className="connection-dot" />AWS Baseline<span className="baseline-profile-model">Local Qwen</span></div>
      <div className="topbar-utilities">
        <button className="text-button theme-toggle" aria-label="Toggle dark theme" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? 'Dark theme' : 'Light theme'}</button>
        <button className="link-status" aria-pressed={connected} onClick={() => setConnected(!connected)} title="Pause display updates; agent execution continues">
          <span className={`connection-dot ${connected ? '' : 'disconnected'}`} />{connected ? 'Auto refresh on' : 'Refresh paused'}
        </button>
      </div>
    </header>
    <main id="workspace" tabIndex={-1}><NativeWorkspace connected={connected} /></main>
    <footer className="baseline-footer"><span>AWS balanced agents · Local Qwen inference</span><span>Baseline observation workspace</span></footer>
  </div>;
}
