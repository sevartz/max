import { createRoot } from 'react-dom/client';
import App from './App';

async function mountApp() {
  const root = document.getElementById('root');
  if (!root) throw new Error('Root element is missing');
  const bridge = window as Window & {
    maxBridgeReady?: Promise<boolean>;
  };
  // The bridge promise has its own bounded startup timeout; still mount the
  // recovery screen when MAX is unavailable so the user can retry in place.
  if (bridge.maxBridgeReady) await bridge.maxBridgeReady.catch(() => false);
  createRoot(root).render(<App />);
}

void mountApp();
