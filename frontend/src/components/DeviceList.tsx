import { type Feeder } from '../api';

interface Props {
  feeders: Feeder[];
  selected: number | null;
  onSelect: (id: number) => void;
}

export default function DeviceList({ feeders, selected, onSelect }: Props) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
      {feeders.map(f => {
        const isSelected = f.id === selected;
        return (
          <button
            key={f.id}
            onClick={() => onSelect(f.id)}
            className={`text-left p-4 rounded-lg border-2 transition-colors ${
              isSelected
                ? 'border-blue-500 bg-blue-50'
                : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
          >
            <h3 className="font-semibold text-lg">{f.name || `Feeder ${f.id}`}</h3>
            <p className="text-sm text-gray-500 capitalize">{f.type}</p>
            <div className="mt-2 flex gap-4 text-sm">
              {f.state?.food != null && (
                <span>Food: {f.state.food}%</span>
              )}
              {f.state?.battery_power != null && (
                <span>Battery: {f.state.battery_power}%</span>
              )}
              {f.state?.online != null && (
                <span className={f.state.online ? 'text-green-600' : 'text-red-500'}>
                  {f.state.online ? 'Online' : 'Offline'}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
