import { useState, useEffect, useCallback } from 'react';
import { type Feeder, fetchFeeders, getApiErrorMessage, refreshAllFeeders } from './api';
import DeviceList from './components/DeviceList';
import FeederDashboard from './components/FeederDashboard';

function App() {
  const [feeders, setFeeders] = useState<Feeder[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Reads Firestore snapshots through the API; never wakes the worker.
  // Returns whether it succeeded, for the polling backoff.
  const load = useCallback(async (): Promise<boolean> => {
    try {
      const data = await fetchFeeders();
      setFeeders(data);
      setSelected(current => current ?? (data.length > 0 ? data[0].id : null));
      setError(null);
      return true;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load feeders');
      return false;
    }
  }, []);

  // Initial load, then poll so status picks up scheduled snapshot refreshes.
  // Backs off when the API is unreachable, like the schedule panel.
  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;
    let consecutiveFailures = 0;
    const BASE_INTERVAL_MS = 30_000;
    const MAX_INTERVAL_MS = 5 * 60_000;

    const poll = async () => {
      const ok = await load();
      if (cancelled) return;
      setLoading(false);
      consecutiveFailures = ok ? 0 : consecutiveFailures + 1;
      const delay = Math.min(BASE_INTERVAL_MS * 2 ** consecutiveFailures, MAX_INTERVAL_MS);
      timeoutId = window.setTimeout(poll, delay);
    };

    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [load]);

  const [loadingFromPetKit, setLoadingFromPetKit] = useState(false);
  const loadFromPetKit = async () => {
    setLoadingFromPetKit(true);
    setError(null);
    try {
      await refreshAllFeeders();
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Failed to load feeders from PetKit'));
    } finally {
      setLoadingFromPetKit(false);
    }
  };

  const selectedFeeder = feeders.find(f => f.id === selected) ?? null;

  return (
    <div className="min-h-screen text-vapor-text">
      <header className="glass-panel px-6 py-4 border-b border-neon-purple/30">
        <h1 className="text-2xl font-bold text-neon-pink text-glow-pink tracking-wider">
          Cyber Pet Feeder Control Panel
        </h1>
      </header>
      <main className="max-w-5xl mx-auto p-6">
        {loading && <p className="text-vapor-muted">Loading feeders...</p>}
        {error && <p className="text-vapor-danger">{error}</p>}
        {!loading && feeders.length === 0 && (
          <div className="space-y-3">
            <p className="text-vapor-muted">
              No feeder data yet. It appears after the first refresh from PetKit.
            </p>
            <button
              onClick={loadFromPetKit}
              disabled={loadingFromPetKit}
              className="text-sm px-3 py-1.5 rounded border border-neon-cyan/40 text-neon-cyan hover:bg-neon-cyan/10 disabled:opacity-50 transition-all duration-300"
            >
              {loadingFromPetKit ? 'Loading from PetKit...' : 'Load feeders from PetKit'}
            </button>
          </div>
        )}
        {!loading && feeders.length > 0 && (
          <>
            <DeviceList feeders={feeders} selected={selected} onSelect={setSelected} />
            {selectedFeeder && (
              <FeederDashboard feeder={selectedFeeder} onRefresh={load} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

export default App;
