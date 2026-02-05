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
            className={`text-left p-4 rounded-lg border transition-all duration-300 ${
              isSelected
                ? 'border-neon-pink neon-glow-pink bg-neon-pink/10'
                : 'border-neon-purple/30 bg-vapor-card hover:border-neon-cyan/50 hover:neon-glow-cyan'
            }`}
          >
            <h3 className="font-semibold text-lg text-neon-cyan">Name: {f.name || `Feeder ${f.id}`}</h3>
            <p className="text-sm text-vapor-muted capitalize">Feeder Model: {f.type}</p>
          </button>
        );
      })}
    </div>
  );
}
