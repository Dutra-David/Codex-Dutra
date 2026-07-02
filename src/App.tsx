import { useState, useCallback } from 'react';
import CodexSimulator from './components/CodexSimulator';
import type { AgentType } from './types';

export default function App() {
  const [activeAgent, setActiveAgent] = useState<AgentType>('assistant');

  const handleAgentChange = useCallback((agent: AgentType) => {
    setActiveAgent(agent);
  }, []);

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-slate-100 font-mono">
      <CodexSimulator
        activeAgent={activeAgent}
        onAgentChange={handleAgentChange}
      />
    </div>
  );
}
