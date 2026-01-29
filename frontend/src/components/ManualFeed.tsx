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
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Amount (grams, in multiples of 5)
        </label>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={5}
            max={50}
            step={5}
            value={amount}
            onChange={e => setAmount(Number(e.target.value))}
            className="flex-1"
          />
          <span className="text-lg font-semibold w-12 text-center">{amount}g</span>
        </div>
      </div>
      <div className="flex gap-3">
        <button
          onClick={handleFeed}
          disabled={loading}
          className="px-6 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? 'Sending...' : 'Feed Now'}
        </button>
        <button
          onClick={handleCancel}
          disabled={loading}
          className="px-6 py-2 bg-red-100 text-red-700 rounded-lg font-medium hover:bg-red-200 disabled:opacity-50"
        >
          Cancel Feed
        </button>
      </div>
      {message && (
        <p className={`text-sm ${message.includes('Failed') ? 'text-red-600' : 'text-green-600'}`}>
          {message}
        </p>
      )}
    </div>
  );
}
