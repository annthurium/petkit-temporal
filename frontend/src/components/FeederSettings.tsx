import { useState } from 'react';
import {
  type Feeder,
  updateSettings,
  resetDesiccant,
  foodReplenished,
  removeSchedule,
  restoreSchedule,
} from '../api';
import { getCapabilities } from '../deviceCapabilities';

interface Props {
  feeder: Feeder;
  onDone: () => void;
}

export default function FeederSettings({ feeder, onDone }: Props) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const settings = feeder.settings;
  const caps = getCapabilities(feeder.type);

  const toggle = async (key: string, current: number | null) => {
    setLoading(true);
    setMessage(null);
    try {
      await updateSettings(feeder.id, { [key]: current ? 0 : 1 });
      setMessage(`Updated ${key}`);
      onDone();
    } catch {
      setMessage(`Failed to update ${key}`);
    } finally {
      setLoading(false);
    }
  };

  const action = async (label: string, fn: () => Promise<unknown>) => {
    setLoading(true);
    setMessage(null);
    try {
      await fn();
      setMessage(`${label} successful`);
      onDone();
    } catch {
      setMessage(`${label} failed`);
    } finally {
      setLoading(false);
    }
  };

  const toggleItems: { key: string; label: string; value: number | null }[] = settings
    ? [
        { key: 'lightMode', label: 'Indicator Light', value: settings.light_mode },
        { key: 'systemSoundEnable', label: 'System Sound', value: settings.system_sound_enable },
        { key: 'feedSound', label: 'Feed Sound', value: settings.feed_sound },
        ...(caps.eatDetection ? [{ key: 'eatNotify', label: 'Eat Notification', value: settings.eat_notify }] : []),
        { key: 'feedNotify', label: 'Feed Notification', value: settings.feed_notify },
        { key: 'foodNotify', label: 'Food Low Notification', value: settings.food_notify },
        { key: 'foodWarn', label: 'Food Warning', value: settings.food_warn },
        ...(caps.eatDetection ? [{ key: 'eatDetection', label: 'Eat Detection', value: settings.eat_detection }] : []),
        ...(caps.moveDetection ? [{ key: 'moveDetection', label: 'Move Detection', value: settings.move_detection }] : []),
        ...(caps.petDetection ? [{ key: 'petDetection', label: 'Pet Detection', value: settings.pet_detection }] : []),
        ...(caps.surplusControl ? [{ key: 'surplusControl', label: 'Surplus Control', value: settings.surplus_control }] : []),
      ]
    : [];

  return (
    <div className="space-y-6">
      {settings && (
        <div>
          <h3 className="text-sm font-semibold text-neon-purple mb-3">Toggle Settings</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {toggleItems.map(item => (
              <div
                key={item.key}
                className="flex items-center justify-between p-3 rounded-lg bg-neon-purple/10 border border-neon-purple/20"
              >
                <span className="text-sm text-vapor-text">{item.label}</span>
                <button
                  onClick={() => toggle(item.key, item.value)}
                  disabled={loading}
                  className={`relative w-11 h-6 rounded-full transition-all duration-300 ${
                    item.value
                      ? 'bg-neon-pink neon-glow-pink'
                      : 'bg-vapor-muted/30'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full transition-transform duration-300 ${
                      item.value
                        ? 'translate-x-5 bg-white'
                        : 'bg-vapor-muted'
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
          {settings.volume != null && (
            <div className="mt-4 p-3 rounded-lg bg-neon-purple/10 border border-neon-purple/20">
              <label className="text-sm block mb-2 text-vapor-text">Volume: {settings.volume}</label>
              <input
                type="range"
                min={0}
                max={10}
                value={settings.volume}
                onChange={async (e) => {
                  setLoading(true);
                  try {
                    await updateSettings(feeder.id, { volume: Number(e.target.value) });
                    onDone();
                  } finally {
                    setLoading(false);
                  }
                }}
                className="w-full"
              />
            </div>
          )}
        </div>
      )}

      <div>
        <h3 className="text-sm font-semibold text-neon-purple mb-3">Actions</h3>
        <div className="flex flex-wrap gap-3">
          <ActionButton
            label="Reset Desiccant"
            disabled={loading}
            onClick={() => action('Reset desiccant', () => resetDesiccant(feeder.id))}
          />
          {caps.foodReplenished && (
            <ActionButton
              label="Food Replenished"
              disabled={loading}
              onClick={() => action('Food replenished', () => foodReplenished(feeder.id))}
            />
          )}
          <ActionButton
            label="Remove Schedule"
            disabled={loading}
            className="text-vapor-danger border-vapor-danger/40 hover:bg-vapor-danger/10"
            onClick={() => action('Remove schedule', () => removeSchedule(feeder.id))}
          />
          <ActionButton
            label="Restore Schedule"
            disabled={loading}
            onClick={() => action('Restore schedule', () => restoreSchedule(feeder.id))}
          />
        </div>
      </div>

      {message && (
        <p className={`text-sm ${message.includes('failed') ? 'text-vapor-danger' : 'text-vapor-success'}`}>
          {message}
        </p>
      )}
    </div>
  );
}

function ActionButton({
  label,
  onClick,
  disabled,
  className = '',
}: {
  label: string;
  onClick: () => void;
  disabled: boolean;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-4 py-2 text-sm rounded-lg border border-neon-cyan/40 font-medium text-neon-cyan hover:bg-neon-cyan/10 hover:neon-glow-cyan disabled:opacity-50 transition-all duration-300 ${className}`}
    >
      {label}
    </button>
  );
}
