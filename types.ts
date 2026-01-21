export interface ServerStatus {
  online: boolean;
  ip: string;
  port: number;
  players: {
    online: number;
    max: number;
  };
  motd: {
    raw: string[];
    clean: string[];
    html: string[];
  };
  version: string;
  icon?: string; 
  latency?: number; // Simulated or fetched latency
}

export interface NavItem {
  label: string;
  href: string;
}

export enum DownloadType {
  WINDOWS = 'WINDOWS',
  MAC = 'MAC',
  LINUX = 'LINUX',
  MOBILE = 'MOBILE'
}