import {createContext, useContext, useState, type ReactNode} from 'react';
import {AgentConversation} from './agent-conversation';

type Agent = 'grokbot' | 'muse';
type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
type Selection = {
  agent: Agent;
  setAgent: (agent: Agent) => void;
  threadIds: Partial<Record<Agent, string>>;
  remember: (agent: Agent, id: string) => void;
};
const CareerChat = createContext<Selection | null>(null);

// Keep navigation state for this sign-in. The conversation service owns the history.
export function CareerChatSession({children}: {children: ReactNode}) {
  const [agent, setAgent] = useState<Agent>('grokbot');
  const [threadIds, setThreadIds] = useState<Selection['threadIds']>({});
  const remember = (agent: Agent, id: string) => setThreadIds(ids => ({...ids, [agent]: id}));
  return <CareerChat.Provider value={{agent, setAgent, threadIds, remember}}>{children}</CareerChat.Provider>;
}

export function CareerConversation({api}: {api: Api}) {
  const selection = useContext(CareerChat);
  if (!selection) throw new Error('Career conversations require a signed-in workspace.');
  const {agent, setAgent, threadIds, remember} = selection;
  return <section className="crm-surface crm-career-section">
    <h2>Agent conversations</h2>
    <div className="crm-filter-tabs" role="group" aria-label="Choose an agent">
      <button aria-pressed={agent === 'grokbot'} onClick={() => setAgent('grokbot')}>Grokbot</button>
      <button aria-pressed={agent === 'muse'} onClick={() => setAgent('muse')}>Muse</button>
    </div>
    <AgentConversation api={api} agent={agent} threadId={threadIds[agent]}
      onThreadChange={id => remember(agent, id)}/>
  </section>;
}
