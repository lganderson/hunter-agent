import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";
import { ChatIcon, PlusIcon, SendIcon, XIcon } from "../components/Icons";
import { clearAgentChatHistory, getAgentChatHistory, getSettings, sendAgentChat } from "../core/api";
import { markdownToHtml } from "../core/format";
import type { AgentChatMessage, AgentContext, AgentToolCall, AppState } from "../core/types";
import { agentExperience } from "./agentContext";

type HunterChatProps = {
  context: AgentContext;
  data: AppState;
  onOpenChange: (open: boolean) => void;
  onPanelWidthChange: (width: number, commit: boolean) => void;
  open: boolean;
  panelWidth: number;
  refresh: () => Promise<AppState>;
};

type ChatDisplayMessage = AgentChatMessage & {
  toolCalls?: AgentToolCall[];
};

const MIN_PANEL_WIDTH = 320;
const MAX_PANEL_WIDTH = 720;
const DEFAULT_PANEL_WIDTH = 400;

function panelWidthMaximum(): number {
  return Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, window.innerWidth - 640));
}

function clampPanelWidth(width: number): number {
  return Math.round(Math.max(MIN_PANEL_WIDTH, Math.min(panelWidthMaximum(), width)));
}

function ChatMessage({ message }: { message: ChatDisplayMessage }) {
  const receipts = message.toolCalls?.filter(call => call.receipt) || [];
  return (
    <div className={`chat-entry ${message.role}`}>
      <div
        className={`chat-message ${message.role}`}
        dangerouslySetInnerHTML={{ __html: markdownToHtml(message.content) }}
      />
      {receipts.length ? (
        <ul className="chat-receipts" aria-label="Hunter activity">
          {receipts.map((call, index) => (
            <li className={call.ok ? "success" : "error"} key={`${call.name}-${index}`}>{call.receipt}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function HunterChat({ context, data, onOpenChange, onPanelWidthChange, open, panelWidth, refresh }: HunterChatProps) {
  const [messages, setMessages] = useState<ChatDisplayMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [historyReady, setHistoryReady] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState<boolean | null>(null);
  const [clearing, setClearing] = useState(false);
  const [sending, setSending] = useState(false);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const resizeHandleRef = useRef<HTMLDivElement | null>(null);
  const panelWidthRef = useRef(panelWidth);
  const experience = useMemo(() => agentExperience(context, data), [context, data]);

  useEffect(() => {
    panelWidthRef.current = panelWidth;
  }, [panelWidth]);

  useEffect(() => () => document.body.classList.remove("chat-panel-resizing"), []);

  useEffect(() => {
    let cancelled = false;
    getAgentChatHistory()
      .then(history => {
        if (cancelled) return;
        setMessages(history.map(message => ({
          role: message.role,
          content: message.content,
          toolCalls: message.tool_calls
        })));
      })
      .catch(error => {
        if (cancelled) return;
        setStatus(error instanceof Error ? error.message : "Could not load chat history.");
      })
      .finally(() => {
        if (!cancelled) setHistoryReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setTokenConfigured(null);
    getSettings()
      .then(settings => {
        if (!cancelled) setTokenConfigured(settings.token_configured);
      })
      .catch(error => {
        if (!cancelled) setStatus(error instanceof Error ? error.message : "Could not check chat setup.");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight });
    inputRef.current?.focus();
  }, [messages, open]);

  async function sendMessage(content: string) {
    content = content.trim();
    if (!content || sending || clearing || !historyReady || tokenConfigured !== true) return;

    setMessages(current => [...current, { role: "user", content }]);
    setDraft("");
    setStatus("");
    setSending(true);
    try {
      const response = await sendAgentChat(content, context);
      setMessages(current => [...current, { role: "assistant", content: response.message, toolCalls: response.tool_calls }]);
      setStatus(response.tool_calls.some(call => !call.ok) ? "Tracker action incomplete." : "");
      if (response.mutated) await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages(current => [...current, { role: "assistant", content: message }]);
      setStatus("Chat request failed.");
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage(draft);
  }

  function chooseStarter(prompt: string) {
    setDraft(prompt);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  async function startNewChat() {
    if (!window.confirm("Clear this conversation and start a new chat?")) return;
    setClearing(true);
    setStatus("");
    try {
      await clearAgentChatHistory();
      setMessages([]);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not clear chat history.");
    } finally {
      setClearing(false);
    }
  }

  function updatePanelWidth(width: number, commit: boolean) {
    const nextWidth = clampPanelWidth(width);
    panelWidthRef.current = nextWidth;
    resizeHandleRef.current?.setAttribute("aria-valuenow", String(nextWidth));
    resizeHandleRef.current?.setAttribute("aria-valuetext", `${nextWidth} pixels`);
    onPanelWidthChange(nextWidth, commit);
  }

  function beginPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add("chat-panel-resizing");
  }

  function resizePanel(event: ReactPointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    updatePanelWidth(window.innerWidth - event.clientX, false);
  }

  function finishPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    document.body.classList.remove("chat-panel-resizing");
    updatePanelWidth(panelWidthRef.current, true);
  }

  function resizePanelWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    let nextWidth = panelWidthRef.current;
    if (event.key === "ArrowLeft") nextWidth += 24;
    else if (event.key === "ArrowRight") nextWidth -= 24;
    else if (event.key === "Home") nextWidth = MIN_PANEL_WIDTH;
    else if (event.key === "End") nextWidth = panelWidthMaximum();
    else return;
    event.preventDefault();
    updatePanelWidth(nextWidth, true);
  }

  if (!open) {
    return (
      <button className="chat-launcher" type="button" onClick={() => onOpenChange(true)} aria-expanded="false" aria-label="Open Hunter chat">
        <ChatIcon size={22} />
      </button>
    );
  }

  return (
    <aside className="chat-panel" aria-label="Hunter chat">
      <div
        ref={resizeHandleRef}
        className="chat-resize-handle"
        role="separator"
        tabIndex={0}
        aria-label="Resize Hunter chat"
        aria-orientation="vertical"
        aria-valuemin={MIN_PANEL_WIDTH}
        aria-valuemax={panelWidthMaximum()}
        aria-valuenow={panelWidth}
        aria-valuetext={`${panelWidth} pixels`}
        onDoubleClick={() => updatePanelWidth(DEFAULT_PANEL_WIDTH, true)}
        onKeyDown={resizePanelWithKeyboard}
        onPointerCancel={finishPanelResize}
        onPointerDown={beginPanelResize}
        onPointerMove={resizePanel}
        onPointerUp={finishPanelResize}
        title="Drag to resize. Double-click to reset."
      />
      <header className="chat-header">
        <div className="chat-header-context">
          <strong>Hunter</strong>
          <span>{context.label || "Ready"}</span>
        </div>
        <div className="chat-header-actions">
          {messages.length ? (
            <button className="icon-button" type="button" onClick={() => void startNewChat()} disabled={sending || clearing} aria-label="New Hunter chat" title="New chat">
              <PlusIcon size={17} />
            </button>
          ) : null}
          <button className="icon-button" type="button" onClick={() => onOpenChange(false)} aria-label="Close Hunter chat">
            <XIcon size={17} />
          </button>
        </div>
      </header>

      <div className="chat-messages" ref={messagesRef} aria-live="polite">
        {!historyReady || tokenConfigured === null ? <div className="chat-message assistant muted">Loading…</div> : null}
        {historyReady && tokenConfigured === true && !messages.length ? (
          <section className="agent-intro" aria-labelledby="agent-intro-title">
            <h2 id="agent-intro-title">{experience.title}</h2>
            <p>{experience.description}</p>
            <div className="agent-starters" aria-label="Suggested prompts">
              {experience.starters.map(starter => (
                <button key={starter.label} type="button" onClick={() => chooseStarter(starter.prompt)}>{starter.label}</button>
              ))}
            </div>
          </section>
        ) : null}
        {messages.map((message, index) => (
          <ChatMessage key={`${message.role}-${index}`} message={message} />
        ))}
        {historyReady && tokenConfigured === false ? (
          <div className="agent-setup">
            <strong>OpenAI API token required.</strong>
            <Link className="button primary compact" to="/settings" onClick={() => onOpenChange(false)}>Open settings</Link>
          </div>
        ) : null}
        {sending ? <div className="chat-message assistant muted">Working…</div> : null}
      </div>

      <form className="chat-composer" onSubmit={submit}>
        <textarea
          ref={inputRef}
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={tokenConfigured === false ? "Add an API token in Settings" : "Ask Hunter..."}
          rows={2}
          disabled={sending || clearing || !historyReady || tokenConfigured !== true}
        />
        <button className="button primary compact" type="submit" disabled={sending || clearing || !historyReady || tokenConfigured !== true || !draft.trim()} aria-label="Send message">
          <SendIcon size={15} />
        </button>
      </form>
      <div className="chat-status" aria-live="polite">{status}</div>
    </aside>
  );
}
