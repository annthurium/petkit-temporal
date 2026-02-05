export interface DeviceCapabilities {
  dualBowl: boolean;
  camera: boolean;
  weightSensor: boolean;
  foodReplenished: boolean;
  eatDetection: boolean;
  moveDetection: boolean;
  petDetection: boolean;
  surplusControl: boolean;
}

const DEFAULTS: DeviceCapabilities = {
  dualBowl: false,
  camera: false,
  weightSensor: false,
  foodReplenished: false,
  eatDetection: false,
  moveDetection: false,
  petDetection: false,
  surplusControl: false,
};

const CAPABILITIES: Record<string, Partial<DeviceCapabilities>> = {
  feeder: {},
  feedermini: {},
  d3: {
    weightSensor: true,
    surplusControl: true,
  },
  d4: {
    weightSensor: true,
  },
  d4s: {
    dualBowl: true,
    weightSensor: true,
    foodReplenished: true,
  },
  d4h: {
    camera: true,
    weightSensor: true,
    foodReplenished: true,
    eatDetection: true,
    moveDetection: true,
    petDetection: true,
  },
  d4sh: {
    dualBowl: true,
    camera: true,
    weightSensor: true,
    foodReplenished: true,
    eatDetection: true,
    moveDetection: true,
    petDetection: true,
  },
};

export function getCapabilities(deviceType: string | null | undefined): DeviceCapabilities {
  if (!deviceType) return { ...DEFAULTS };
  const overrides = CAPABILITIES[deviceType];
  if (!overrides) return { ...DEFAULTS };
  return { ...DEFAULTS, ...overrides };
}
