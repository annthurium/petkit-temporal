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
    setLoading(true);
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
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const selectedFeeder = feeders.find(f => f.id === selected) ?? null;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <h1 className="text-2xl font-bold">PetKit Feeder Control</h1>
      </header>
      <main className="max-w-5xl mx-auto p-6">
        {loading && <p className="text-gray-500">Loading feeders...</p>}
        {error && <p className="text-red-600">{error}</p>}
        {!loading && !error && feeders.length === 0 && (
          <p className="text-gray-500">No feeders found on your account.</p>
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
