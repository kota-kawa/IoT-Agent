export type AppConfig = {
  apiBase?: string;
};

export type NoticeKind = 'info' | 'error' | 'success';

export type NoticeState = {
  message: string;
  kind: NoticeKind;
  visible: boolean;
};

export type ChatRole = 'assistant' | 'user' | 'system';

export type ChatImage = {
  data_url?: string;
  label?: string;
};

export type ChatMessage = {
  role: ChatRole;
  content: string;
  time: string;
  images: ChatImage[];
};

export type ModelOption = {
  provider: string;
  model: string;
  label?: string;
  base_url?: string;
};

export type DeviceCapability = {
  name?: string;
  [key: string]: unknown;
};

export type DeviceMeta = {
  display_name?: string;
  note?: string;
  label?: string;
  location?: string;
  virtual?: boolean;
  [key: string]: unknown;
};

export type DeviceResult = {
  ok?: boolean;
  job_id?: string;
  return_value?: unknown;
  message?: string;
  [key: string]: unknown;
};

export type Device = {
  device_id: string;
  capabilities?: DeviceCapability[];
  queue_depth?: number | string;
  last_seen?: number | string;
  registered_at?: number | string;
  last_result?: DeviceResult | null;
  meta?: DeviceMeta;
};

export type DeviceListResponse = {
  devices?: Device[];
  error?: string;
  message?: string;
};

export type DeviceUpdateResponse = {
  device?: Device;
  error?: string;
  message?: string;
};

export type DeviceRegisterResponse = {
  device_id?: string;
  device?: Device;
  error?: string;
  message?: string;
};

export type GenericResponse = {
  error?: string;
  message?: string;
  [key: string]: unknown;
};

export type ChatResponse = {
  reply?: string;
  images?: ChatImage[];
  error?: string;
  message?: string;
};

export type ModelsResponse = {
  models?: ModelOption[];
  current?: ModelOption;
  error?: string;
  message?: string;
};

export type ModelSettingsPayload = {
  provider: string;
  model: string;
  base_url: string;
};

export type DeviceRegisterPayload = {
  device_id: string;
  capabilities: DeviceCapability[];
  meta: {
    registered_via: string;
    display_name?: string;
    note?: string;
  };
  approved: boolean;
};

export type RegisterSuccess = {
  deviceId: string;
  label: string;
  displayName: string;
};
