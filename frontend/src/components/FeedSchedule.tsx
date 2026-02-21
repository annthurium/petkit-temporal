import { useState, useEffect } from 'react';
import {
  type FeedSchedule,
  deleteSchedule,
  fetchSchedule,
  getApiErrorMessage,
  setSchedule,
} from '../api';

interface Props {
  feederId: number;
}

export default function FeedSchedulePanel({ feederId }: Props) {
  const [schedule, setScheduleState] = useState<FeedSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [time, setTime] = useState('08:00');
  const [amount, setAmount] = useState(10);

  useEffect(() => {
    let isMounted = true;
    let hasInitialized = false;

    const loadSchedule = async () => {
      if (!hasInitialized && isMounted) {
        setLoading(true);
      }
      try {
        const s = await fetchSchedule(feederId);
        if (!isMounted) return;
        setScheduleState(s);
        if (s) {
          setTime(s.time);
          setAmount(s.amount);
        }
      } catch {
        if (!isMounted) return;
        setScheduleState(null);
      } finally {
        if (!hasInitialized && isMounted) {
          setLoading(false);
        }
        hasInitialized = true;
      }
    };

    loadSchedule();
    const interval = window.setInterval(loadSchedule, 5000);

    return () => {
      isMounted = false;
      window.clearInterval(interval);
    };
  }, [feederId]);

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
      {schedule && (
        <div className="p-4 rounded-lg bg-neon-purple/10 border border-neon-purple/20 space-y-1">
          <p className="text-sm text-vapor-muted">Current schedule</p>
          <p className="text-lg font-semibold text-neon-cyan text-glow-cyan">
            {schedule.amount}g daily at {schedule.time}
          </p>
          {schedule.skip_next && (
            <p className="text-sm text-neon-pink">Next feeding will be skipped (manual feed detected)</p>
          )}
          {schedule.last_alert && (
            <div className="mt-3 p-3 rounded-lg bg-vapor-danger/10 border border-vapor-danger/30 space-y-1">
              <p className="text-sm font-semibold text-vapor-danger">Feed Alert</p>
              <p className="text-sm text-vapor-danger">{schedule.last_alert.reason}</p>
              <p className="text-xs text-vapor-muted">
                {new Date(schedule.last_alert.timestamp).toLocaleString()}
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
    </div>
  );
}
