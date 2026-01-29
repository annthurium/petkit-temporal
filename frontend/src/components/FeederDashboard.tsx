import { useState } from 'react';
import { type Feeder, refreshFeeder } from '../api';
import ManualFeed from './ManualFeed';
import FeedingHistory from './FeedingHistory';
import FeederSettings from './FeederSettings';

interface Props {
  feeder: Feeder;
  onRefresh: () => void;
}

type Tab = 'status' | 'feed' | 'history' | 'settings';

export default function FeederDashboard({ feeder, onRefresh }: Props) {
  const [tab, setTab] = useState<Tab>('status');
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refreshFeeder(feeder.id);
      onRefresh();
    } finally {
      setRefreshing(false);
    }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'status', label: 'Status' },
    { key: 'feed', label: 'Manual Feed' },
    { key: 'history', label: 'History' },
    { key: 'settings', label: 'Settings' },
  ];

  const s = feeder.state;
  const fs = feeder.feed_state;

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <div className="flex gap-1">
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm rounded-md font-medium transition-colors ${
                tab === t.key
                  ? 'bg-blue-100 text-blue-700'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="text-sm px-3 py-1.5 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50"
        >
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <div className="p-6">
        {tab === 'status' && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <StatusCard label="Food Level" value={s?.food != null ? `${s.food}%` : '--'} />
            <StatusCard label="Food (Bowl 1)" value={s?.food1 != null ? `${s.food1}` : '--'} />
            <StatusCard label="Food (Bowl 2)" value={s?.food2 != null ? `${s.food2}` : '--'} />
            <StatusCard label="Battery" value={s?.battery_power != null ? `${s.battery_power}%` : '--'} />
            <StatusCard label="Desiccant Days Left" value={s?.desiccant_left_days != null ? `${s.desiccant_left_days}` : '--'} />
            <StatusCard label="Weight" value={s?.weight != null ? `${s.weight}g` : '--'} />
            <StatusCard label="Eating" value={s?.eating ? 'Yes' : 'No'} />
            <StatusCard label="Feeding" value={s?.feeding ? 'Yes' : 'No'} />
            <StatusCard label="Online" value={s?.online ? 'Yes' : 'No'} />
            {s?.error_msg && (
              <div className="col-span-full p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
                Error: {s.error_msg} (code: {s.error_code})
              </div>
            )}
            {fs && (
              <>
                <StatusCard label="Eat Count (Today)" value={fs.eat_count != null ? `${fs.eat_count}` : '--'} />
                <StatusCard label="Avg per Eat" value={fs.eat_avg != null ? `${fs.eat_avg}g` : '--'} />
                <StatusCard label="Planned Total" value={fs.plan_amount_total != null ? `${fs.plan_amount_total}g` : '--'} />
                <StatusCard label="Actual Dispensed" value={fs.real_amount_total != null ? `${fs.real_amount_total}g` : '--'} />
              </>
            )}
          </div>
        )}
        {tab === 'feed' && <ManualFeed feederId={feeder.id} onDone={onRefresh} />}
        {tab === 'history' && <FeedingHistory feederId={feeder.id} />}
        {tab === 'settings' && <FeederSettings feeder={feeder} onDone={onRefresh} />}
      </div>
    </div>
  );
}

function StatusCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 bg-gray-50 rounded-lg">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}
