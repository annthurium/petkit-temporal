import { useState } from 'react';
import { type Feeder, refreshFeeder } from '../api';
import { getCapabilities } from '../deviceCapabilities';
import ManualFeed from './ManualFeed';
import FeedSchedulePanel from './FeedSchedule';
import FeedingHistory from './FeedingHistory';
import FeederSettings from './FeederSettings';

interface Props {
  feeder: Feeder;
  onRefresh: () => void;
}

type Tab = 'status' | 'feed' | 'schedule' | 'history' | 'settings';

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
    { key: 'schedule', label: 'Schedule' },
    { key: 'history', label: 'History' },
    { key: 'settings', label: 'Settings' },
  ];

  const s = feeder.state;
  const fs = feeder.feed_state;
  const caps = getCapabilities(feeder.type);

  return (
    <div className="glass-panel rounded-lg">
      <div className="flex items-center justify-between border-b border-neon-purple/20 px-4 py-3">
        <div className="flex gap-1">
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm rounded-md font-medium transition-all duration-300 ${
                tab === t.key
                  ? 'bg-neon-pink/20 text-neon-pink neon-glow-pink'
                  : 'text-vapor-muted hover:text-neon-cyan hover:bg-neon-cyan/10'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="text-sm px-3 py-1.5 rounded border border-neon-cyan/40 text-neon-cyan hover:bg-neon-cyan/10 hover:neon-glow-cyan disabled:opacity-50 transition-all duration-300"
        >
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <div className="p-6">
        {tab === 'status' && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <StatusCard label="Food Level" value={s?.food != null ? `${s.food}%` : '--'} />
            {caps.dualBowl && (
              <>
                <StatusCard label="Food (Bowl 1)" value={s?.food1 != null ? `${s.food1}` : '--'} />
                <StatusCard label="Food (Bowl 2)" value={s?.food2 != null ? `${s.food2}` : '--'} />
              </>
            )}
            <StatusCard label="Battery" value={s?.battery_power != null ? `${s.battery_power}%` : '--'} />
            <StatusCard label="Desiccant Days Left" value={s?.desiccant_left_days != null ? `${s.desiccant_left_days}` : '--'} />
            {caps.weightSensor && (
              <StatusCard label="Weight" value={s?.weight != null ? `${s.weight}g` : '--'} />
            )}
            {caps.eatDetection && (
              <>
                <StatusCard label="Eating" value={s?.eating ? 'Yes' : 'No'} />
                <StatusCard label="Feeding" value={s?.feeding ? 'Yes' : 'No'} />
              </>
            )}
            <StatusCard label="Online" value={s?.online ? 'Yes' : 'No'} />
            {s?.error_msg && (
              <div className="col-span-full p-3 bg-vapor-danger/10 border border-vapor-danger/30 rounded text-vapor-danger text-sm">
                Error: {s.error_msg} (code: {s.error_code})
              </div>
            )}
            {fs && (
              <>
                {caps.eatDetection && (
                  <>
                    <StatusCard label="Eat Count (Today)" value={fs.eat_count != null ? `${fs.eat_count}` : '--'} />
                    <StatusCard label="Avg per Eat" value={fs.eat_avg != null ? `${fs.eat_avg}g` : '--'} />
                  </>
                )}
                <StatusCard label="Planned Total" value={fs.plan_amount_total != null ? `${fs.plan_amount_total}g` : '--'} />
                <StatusCard label="Actual Dispensed" value={fs.real_amount_total != null ? `${fs.real_amount_total}g` : '--'} />
              </>
            )}
          </div>
        )}
        {tab === 'feed' && <ManualFeed feederId={feeder.id} onDone={onRefresh} />}
        {tab === 'schedule' && <FeedSchedulePanel feederId={feeder.id} />}
        {tab === 'history' && <FeedingHistory feederId={feeder.id} hasEatDetection={caps.eatDetection} />}
        {tab === 'settings' && <FeederSettings feeder={feeder} onDone={onRefresh} />}
      </div>
    </div>
  );
}

function StatusCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 rounded-lg bg-neon-purple/10 border border-neon-purple/20">
      <p className="text-xs text-vapor-muted mb-1">{label}</p>
      <p className="text-lg font-semibold text-neon-cyan text-glow-cyan">{value}</p>
    </div>
  );
}
