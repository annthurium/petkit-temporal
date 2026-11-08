import axios from 'axios';

// In production the app sits behind Identity-Aware Proxy (ADR 0010). When the
// IAP session expires, IAP answers script requests that carry this header with
// 401 instead of a redirect to Google sign-in (which fetch can't follow).
const api = axios.create({
  baseURL: '/api',
  headers: { 'X-Requested-With': 'XMLHttpRequest' },
});

// On 401, reload the page so IAP can run the normal sign-in redirect.
// Rate-limited so a persistent 401 (e.g. no access) can't cause a reload loop.
const IAP_RELOAD_KEY = 'iap-reauth-at';
api.interceptors.response.use(undefined, (error) => {
  if (axios.isAxiosError(error) && error.response?.status === 401) {
    const last = Number(sessionStorage.getItem(IAP_RELOAD_KEY) ?? 0);
    if (Date.now() - last > 60_000) {
      sessionStorage.setItem(IAP_RELOAD_KEY, String(Date.now()));
      window.location.reload();
    }
  }
  return Promise.reject(error);
});

export interface FeederState {
  battery_power: number | null;
  battery_status: number | null;
  food: number | null;
  food1: number | null;
  food2: number | null;
  eating: number | null;
  feeding: number | null;
  desiccant_left_days: number | null;
  error_code: string | null;
  error_msg: string | null;
  weight: number | null;
  pim: number | null;
  online: number | null;
}

export interface FeederSettings {
  light_mode: number | null;
  light_multi_range: unknown;
  volume: number | null;
  feed_sound: number | null;
  system_sound_enable: number | null;
  tone_mode: number | null;
  eat_notify: number | null;
  feed_notify: number | null;
  food_notify: number | null;
  food_warn: number | null;
  food_warn_range: unknown;
  eat_detection: number | null;
  eat_sensitivity: number | null;
  move_detection: number | null;
  pet_detection: number | null;
  surplus_control: number | null;
  surplus_standard: number | null;
  camera: number | null;
}

export interface FeedState {
  eat_count: number | null;
  eat_avg: number | null;
  add_amount_total: number | null;
  plan_amount_total: number | null;
  real_amount_total: number | null;
}

export interface Feeder {
  id: number;
  name: string;
  type: string;
  firmware: string;
  state: FeederState | null;
  settings: FeederSettings | null;
  feed_state: FeedState | null;
  manual_feed: { amount: number | null; amount1: number | null; amount2: number | null; time: number | null; status: number | null } | null;
  // When the worker last wrote this snapshot from PetKit (ISO 8601, UTC).
  updated_at?: string;
}

export interface FeederRecords {
  eat: Record<string, unknown>[];
  feed: Record<string, unknown>[];
  move: Record<string, unknown>[];
  pet: Record<string, unknown>[];
}

export const fetchFeeders = () => api.get<Feeder[]>('/feeders').then(r => r.data);
export const fetchFeeder = (id: number) => api.get<Feeder>(`/feeders/${id}`).then(r => r.data);
export const fetchRecords = (id: number) => api.get<FeederRecords>(`/feeders/${id}/records`).then(r => r.data);

export interface ManualFeedResponse {
  status: string;
  via: 'workflow';
  workflow_id: string;
}

export const manualFeed = (id: number, payload: { amount?: number; amount1?: number; amount2?: number }) =>
  api.post<ManualFeedResponse>(`/feeders/${id}/feed`, payload).then(r => r.data);
// Device commands run as Temporal workflows. "pending" means the request
// stopped waiting before the workflow finished; the command is still running.
export interface DeviceCommandResponse {
  status: 'ok' | 'pending';
  workflow_id: string;
}

export const updateSettings = (id: number, settings: Record<string, unknown>) =>
  api.post<DeviceCommandResponse>(`/feeders/${id}/settings`, { settings }).then(r => r.data);
export const resetDesiccant = (id: number) =>
  api.post<DeviceCommandResponse>(`/feeders/${id}/desiccant/reset`).then(r => r.data);
export const foodReplenished = (id: number) =>
  api.post<DeviceCommandResponse>(`/feeders/${id}/food-replenished`).then(r => r.data);
export const refreshFeeder = (id: number) => api.post<Feeder>(`/feeders/${id}/refresh`).then(r => r.data);
// Loads every feeder on the account from PetKit into Firestore (via the worker).
export const refreshAllFeeders = () => api.post(`/feeders/refresh`).then(r => r.data);

export interface FeedAlert {
  device_id: number;
  reason: string;
  timestamp: string;
}

export interface FeedResult {
  status: 'success' | 'failure' | 'skipped';
  feed_type: 'scheduled' | 'manual';
  message: string;
  timestamp: string;
  attempts: number;
}

export interface FeedSchedule {
  time: string;
  amount: number | null;
  timezone: string | null;
  next_run: string | null;
  paused: boolean;
  skip_window_hours: number;
  last_alert: FeedAlert | null;
  last_feed_result: FeedResult | null;
}

export const fetchSchedule = (id: number) => api.get<FeedSchedule | null>(`/feeders/${id}/schedule`).then(r => r.data);
export const setSchedule = (id: number, payload: { time: string; amount: number }) =>
  api.put<FeedSchedule>(`/feeders/${id}/schedule`, payload).then(r => r.data);
export const deleteSchedule = (id: number) => api.delete(`/feeders/${id}/schedule`).then(r => r.data);

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim().length > 0) {
      return detail;
    }

    if (typeof error.message === 'string' && error.message.trim().length > 0) {
      return error.message;
    }
  }

  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message;
  }

  return fallback;
}
