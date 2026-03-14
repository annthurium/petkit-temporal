import { useState, useEffect } from 'react';
import {
  type FeedSchedule,
  deleteSchedule,
  fetchSchedule,
  getApiErrorMessage,
  manualFeed,
  setSchedule,
} from '../api';

interface Props {
  feederId: number;
  onDone: () => void;
}

export default function FeedSchedulePanel({ feederId, onDone }: Props) {
  const [schedule, setScheduleState] = useState<FeedSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [backendReachable, setBackendReachable] = useState(true);
  const [time, setTime] = useState('08:00');
  const [amount, setAmount] = useState(10);

  const [feedAmount, setFeedAmount] = useState(5);
  const [feedLoading, setFeedLoading] = useState(false);
  const [feedMessage, setFeedMessage] = useState<string | null>(null);
  const [feedMessageTone, setFeedMessageTone] = useState<'success' | 'error' | 'info'>('success');

  useEffect(() => {
    let isMounted = true;
    let hasInitialized = false;
    let timeoutId: number | undefined;
    let consecutiveFailures = 0;

    const BASE_INTERVAL_MS = 30_000;
    const MAX_INTERVAL_MS = 5 * 60_000;

    const getNextInterval = () => {
      if (consecutiveFailures === 0) return BASE_INTERVAL_MS;
      return Math.min(BASE_INTERVAL_MS * 2 ** consecutiveFailures, MAX_INTERVAL_MS);
    };

    const loadSchedule = async () => {
      if (!hasInitialized && isMounted) {
        setLoading(true);
      }
      try {
        const s = await fetchSchedule(feederId);
        if (!isMounted) return;
        setScheduleState(s);
        setBackendReachable(true);
        consecutiveFailures = 0;
        if (s) {
          setTime(s.time);
          setAmount(s.amount);
        }
      } catch {
        if (!isMounted) return;
        setBackendReachable(false);
        consecutiveFailures++;
      } finally {
        if (!hasInitialized && isMounted) {
          setLoading(false);
        }
        hasInitialized = true;
        if (isMounted) {
          timeoutId = window.setTimeout(loadSchedule, getNextInterval());
        }
      }
    };

    loadSchedule();

    return () => {
      isMounted = false;
      window.clearTimeout(timeoutId);
    };
  }, [feederId]);

  const handleFeed = async () => {
    setFeedLoading(true);
    setFeedMessage(null);
    try {
      const result = await manualFeed(feederId, { amount: feedAmount });
      if (result.via === 'workflow') {
        setFeedMessageTone('info');
        setFeedMessage('Feed request sent. Waiting for device confirmation.');
      } else {
        setFeedMessageTone('success');
        setFeedMessage(`Fed ${feedAmount}g successfully`);
      }
      onDone();
    } catch (error: unknown) {
      setFeedMessageTone('error');
      setFeedMessage(getApiErrorMessage(error, 'Failed to feed'));
    } finally {
      setFeedLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const s = await setSchedule(feederId, { time, amount });
      setScheduleState(s);
      setMessage('Schedule saved');
    } catch (error: unknown) {
      setMessage(getApiErrorMessage(error, 'Failed to save schedule'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await deleteSchedule(feederId);
      setScheduleState(null);
      setMessage('Schedule removed');
    } catch (error: unknown) {
      setMessage(getApiErrorMessage(error, 'Failed to remove schedule'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <p className="text-vapor-muted text-sm">Loading schedule...</p>;
  }

  return (
    <div className="space-y-5">
      {!backendReachable && (
        <div className="p-3 rounded-lg bg-vapor-danger/10 border border-vapor-danger/30">
          <p className="text-sm text-vapor-danger font-semibold">Backend unreachable</p>
          <p className="text-xs text-vapor-danger/80">Showing last known state. The schedule is still running in Temporal.</p>
        </div>
      )}
      {schedule && (
        <div className="p-4 rounded-lg bg-neon-purple/10 border border-neon-purple/20 space-y-1">
          <p className="text-sm text-vapor-muted">Current schedule</p>
          <p className="text-lg font-semibold text-neon-cyan text-glow-cyan">
            {schedule.amount}g daily at {schedule.time}
          </p>
          {schedule.skip_next && (
            <p className="text-sm text-neon-pink">Next feeding will be skipped (manual feed detected)</p>
          )}
          {schedule.last_feed_result && (
            <div
              className={`mt-3 p-3 rounded-lg space-y-1 border ${
                schedule.last_feed_result.status === 'success'
                  ? 'bg-vapor-success/10 border-vapor-success/30'
                  : schedule.last_feed_result.status === 'failure'
                    ? 'bg-vapor-danger/10 border-vapor-danger/30'
                    : 'bg-neon-cyan/10 border-neon-cyan/30'
              }`}
            >
              <p
                className={`text-sm font-semibold ${
                  schedule.last_feed_result.status === 'success'
                    ? 'text-vapor-success'
                    : schedule.last_feed_result.status === 'failure'
                      ? 'text-vapor-danger'
                      : 'text-neon-cyan'
                }`}
              >
                Most Recent Feed: {
                  schedule.last_feed_result.status === 'success'
                    ? 'Success'
                    : schedule.last_feed_result.status === 'failure'
                      ? 'Failed'
                      : 'Unconfirmed'
                }
              </p>
              <p
                className={`text-sm ${
                  schedule.last_feed_result.status === 'success'
                    ? 'text-vapor-success'
                    : schedule.last_feed_result.status === 'failure'
                      ? 'text-vapor-danger'
                      : 'text-neon-cyan'
                }`}
              >
                {schedule.last_feed_result.message}
              </p>
              <p className="text-xs text-vapor-muted">
                {new Date(schedule.last_feed_result.timestamp).toLocaleString()}
              </p>
            </div>
          )}
        </div>
      )}

      <div className="space-y-4 p-4 rounded-xl bg-neon-purple/5 border border-neon-purple/15">
        <div>
          <label className="block text-sm font-medium text-vapor-muted mb-2">
            Time of day
          </label>
          <input
            type="time"
            value={time}
            onChange={e => setTime(e.target.value)}
          />
        </div>

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
      </div>

      <div className="flex gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 bg-neon-pink/20 text-neon-pink border border-neon-pink/40 rounded-lg font-medium hover:bg-neon-pink/30 hover:neon-glow-pink disabled:opacity-50 transition-all duration-300"
        >
          {saving ? 'Saving...' : schedule ? 'Update Schedule' : 'Set Schedule'}
        </button>
        {schedule && (
          <button
            onClick={handleDelete}
            disabled={saving}
            className="px-6 py-2 bg-vapor-danger/10 text-vapor-danger border border-vapor-danger/40 rounded-lg font-medium hover:bg-vapor-danger/20 disabled:opacity-50 transition-all duration-300"
          >
            Remove Schedule
          </button>
        )}
      </div>

      {message && (
        <p className={`text-sm ${message.includes('Failed') ? 'text-vapor-danger' : 'text-vapor-success'}`}>
          {message}
        </p>
      )}

      <div className="border-t border-neon-purple/20 pt-5 mt-5 space-y-4">
        <p className="text-sm font-medium text-vapor-muted">Feed now</p>
        <div>
          <label className="block text-sm font-medium text-vapor-muted mb-2">
            Amount
          </label>
          <select
            value={feedAmount}
            onChange={e => setFeedAmount(Number(e.target.value))}
          >
            {[5, 10, 15, 20, 25, 30].map(g => (
              <option key={g} value={g}>{g}g</option>
            ))}
          </select>
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleFeed}
            disabled={feedLoading}
            className="px-6 py-2 bg-neon-pink/20 text-neon-pink border border-neon-pink/40 rounded-lg font-medium hover:bg-neon-pink/30 hover:neon-glow-pink disabled:opacity-50 transition-all duration-300"
          >
            {feedLoading ? 'Sending...' : 'Feed Now'}
          </button>
        </div>
        {feedMessage && (
          <p
            className={`text-sm ${
              feedMessageTone === 'error'
                ? 'text-vapor-danger'
                : feedMessageTone === 'info'
                  ? 'text-neon-cyan'
                  : 'text-vapor-success'
            }`}
          >
            {feedMessage}
          </p>
        )}
      </div>
    </div>
  );
}
