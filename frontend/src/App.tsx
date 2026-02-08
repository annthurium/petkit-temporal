import { useState, useEffect } from 'react';
import { type Feeder, fetchFeeders } from './api';
import DeviceList from './components/DeviceList';
import FeederDashboard from './components/FeederDashboard';

function App() {
  const [feeders, setFeeders] = useState<Feeder[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const isInitial = feeders.length === 0;
    if (isInitial) setLoading(true);
    setError(null);
    try {
      const data = await fetchFeeders();
      setFeeders(data);
      if (data.length > 0 && selected === null) {
        setSelected(data[0].id);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load feeders');
    } finally {
      if (isInitial) setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

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
        {!loading && !error && feeders.length === 0 && (
          <p className="text-vapor-muted">No feeders found on your account.</p>
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
