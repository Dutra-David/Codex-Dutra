// Tipos centrais do Codex Dutra

export type AgentType = 'assistant' | 'admin' | 'finance' | 'support' | 'programmer';

export type MessageRole = 'user' | 'assistant' | 'system';

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: Date;
  agent?: AgentType;
}

export interface OllamaModel {
  name: string;
  size: number;
  digest: string;
  modified_at: string;
}

export interface OllamaStatus {
  running: boolean;
  models: OllamaModel[];
  error?: string;
}

export interface HardwareProfile {
  cpu: {
    name: string;
    cores: number;
    threads: number;
  };
  ram_gb: number;
  gpu?: {
    name: string;
    vram_gb: number;
  };
  recommended_model: string;
  quantization: string;
  gpu_layers: number;
  threads: number;
  flash_attention: boolean;
  estimated_speed_tps: number;
}

export interface AgentConfig {
  id: AgentType;
  name: string;
  description: string;
  systemPrompt: string;
  icon: string;
  color: string;
}

export interface ChatSession {
  id: string;
  agent: AgentType;
  messages: Message[];
  createdAt: Date;
  updatedAt: Date;
}

export interface AutomationCommand {
  type: 'browser' | 'file' | 'system' | 'window';
  action: string;
  params: Record<string, unknown>;
}

export interface BackendStatus {
  running: boolean;
  port: number;
  version: string;
  error?: string;
}
