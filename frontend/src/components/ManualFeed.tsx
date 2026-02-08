import { useState } from 'react';
import { manualFeed, cancelFeed } from '../api';

interface Props {
  feederId: number;
  onDone: () => void;
}

export default function ManualFeed({ feederId, onDone }: Props) {
  const [amount, setAmount] = useState(5);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const handleFeed = async () => {
    setLoading(true);
    setMessage(null);
    try {
      await manualFeed(feederId, { amount });
      setMessage(`Fed ${amount}g successfully`);
      onDone();
    } catch {
      setMessage('Failed to feed');
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    setLoading(true);
    setMessage(null);
    try {
      await cancelFeed(feederId);
      setMessage('Feed cancelled');
      onDone();
    } catch {
      setMessage('Failed to cancel feed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-vapor-muted mb-2">
          Amount
        </label>
        <select
          value={amount}
          onChange={e => setAmount(Number(e.target.value))}
        >
          {[5, 10, 15, 20, 25, 30].map(g => (
            <option key={g} value={g}>{g}g</option>
          ))}
        </select>
      </div>
      <div className="flex gap-3">
        <button
          onClick={handleFeed}
          disabled={loading}
          className="px-6 py-2 bg-neon-pink/20 text-neon-pink border border-neon-pink/40 rounded-lg font-medium hover:bg-neon-pink/30 hover:neon-glow-pink disabled:opacity-50 transition-all duration-300"
        >
          {loading ? 'Sending...' : 'Feed Now'}
        </button>
        <button
          onClick={handleCancel}
          disabled={loading}
          className="px-6 py-2 bg-vapor-danger/10 text-vapor-danger border border-vapor-danger/40 rounded-lg font-medium hover:bg-vapor-danger/20 disabled:opacity-50 transition-all duration-300"
        >
          Cancel Feed
        </button>
      </div>
      {message && (
        <p className={`text-sm ${message.includes('Failed') ? 'text-vapor-danger' : 'text-vapor-success'}`}>
          {message}
        </p>
      )}
    </div>
  );
}
