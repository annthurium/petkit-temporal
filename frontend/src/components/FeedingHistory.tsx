import { useEffect, useState } from 'react';
import { fetchRecords, type FeederRecords } from '../api';

interface Props {
  feederId: number;
  hasEatDetection: boolean;
}

export default function FeedingHistory({ feederId, hasEatDetection }: Props) {
  const [records, setRecords] = useState<FeederRecords | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'eat' | 'feed'>('feed');

  useEffect(() => {
    setLoading(true);
    console.log('[FeedingHistory] fetching records for feederId:', feederId);
    fetchRecords(feederId)
      .then((data) => {
        console.log('[FeedingHistory] raw API response:', JSON.stringify(data, null, 2));
        console.log('[FeedingHistory] record counts —',
          'eat:', data?.eat?.length, 'feed:', data?.feed?.length,
          'move:', data?.move?.length, 'pet:', data?.pet?.length);
        setRecords(data);
      })
      .catch((err) => {
        console.error('[FeedingHistory] fetch error:', err);
      })
      .finally(() => setLoading(false));
  }, [feederId]);

  if (loading) return <p className="text-vapor-muted">Loading records...</p>;
  if (!records) {
    console.warn('[FeedingHistory] records state is null — showing "No records available"');
    return <p className="text-vapor-muted">No records available.</p>;
  }

  const items = tab === 'feed' ? records.feed : records.eat;
  console.log(`[FeedingHistory] rendering tab="${tab}", items count:`, items.length);

  return (
    <div>
      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setTab('feed')}
          className={`px-3 py-1 text-sm rounded transition-all duration-300 ${
            tab === 'feed'
              ? 'bg-neon-pink/20 text-neon-pink neon-glow-pink'
              : 'text-vapor-muted hover:text-neon-cyan hover:bg-neon-cyan/10'
          }`}
        >
          Feed Events
        </button>
        {hasEatDetection && (
          <button
            onClick={() => setTab('eat')}
            className={`px-3 py-1 text-sm rounded transition-all duration-300 ${
              tab === 'eat'
                ? 'bg-neon-pink/20 text-neon-pink neon-glow-pink'
                : 'text-vapor-muted hover:text-neon-cyan hover:bg-neon-cyan/10'
            }`}
          >
            Eat Events
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <p className="text-vapor-muted text-sm">No {tab} events recorded.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neon-purple/30">
                {Object.keys(items[0]).map(key => (
                  <th key={key} className="text-left py-2 px-3 font-medium text-neon-purple">
                    {key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => (
                <tr key={i} className="border-b border-neon-purple/10 hover:bg-neon-purple/5 transition-colors">
                  {Object.values(item).map((val, j) => (
                    <td key={j} className="py-2 px-3 text-vapor-text">
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
