import { useEffect, useState } from 'react';
import { fetchRecords, type FeederRecords } from '../api';

interface Props {
  feederId: number;
}

export default function FeedingHistory({ feederId }: Props) {
  const [records, setRecords] = useState<FeederRecords | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'eat' | 'feed'>('feed');

  useEffect(() => {
    setLoading(true);
    fetchRecords(feederId)
      .then(setRecords)
      .finally(() => setLoading(false));
  }, [feederId]);

  if (loading) return <p className="text-gray-500">Loading records...</p>;
  if (!records) return <p className="text-gray-500">No records available.</p>;

  const items = tab === 'feed' ? records.feed : records.eat;

  return (
    <div>
      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setTab('feed')}
          className={`px-3 py-1 text-sm rounded ${tab === 'feed' ? 'bg-blue-100 text-blue-700' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          Feed Events
        </button>
        <button
          onClick={() => setTab('eat')}
          className={`px-3 py-1 text-sm rounded ${tab === 'eat' ? 'bg-blue-100 text-blue-700' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          Eat Events
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm">No {tab} events recorded.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                {Object.keys(items[0]).map(key => (
                  <th key={key} className="text-left py-2 px-3 font-medium text-gray-600">
                    {key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => (
                <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                  {Object.values(item).map((val, j) => (
                    <td key={j} className="py-2 px-3">
                      {val == null ? '--' : String(val)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
