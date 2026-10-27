import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
// One stylesheet for both consoles while pages move over: its tokens, theme
// presets and shared classes stay in web/app.css until the classic console
// is retired, then move here.
import '../../web/app.css';
import './styles/app.css';
import './styles/question.css';
import './styles/galaxy.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
