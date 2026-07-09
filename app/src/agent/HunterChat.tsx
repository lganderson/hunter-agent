import { FormEvent, useEffect, useRef, useState } from "react";
import { ChatIcon, SendIcon, XIcon } from "../components/Icons";
import { sendAgentChat } from "../core/api";
import { markdownToHtml } from "../core/format";
import type { AgentChatMessage, AppState } from "../core/types";

type HunterChatProps = {
  refresh: () => Promise<AppState>;
};

const welcomeMessage: AgentChatMessage = {
  role: "assistant",
  content: "Ask me about your postings, actions, or contacts. I can update Hunter when your request is clear."
};

function ChatMessage({ message }: { message: AgentChatMessage }) {
  return (
    <div
      className={`chat-message ${message.role}`}
      dangerouslySetInnerHTML={{ __html: markdownToHtml(message.content) }}
    />
  );
}

export function HunterChat({ refresh }: HunterChatProps) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<AgentChatMessage[]>([welcomeMessage]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [sending, setSending] = useState(false);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight });
    inputRef.current?.focus();
  }, [messages, open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || sending) return;

    const nextMessages = [...messages, { role: "user" as const, content }];
    setMessages(nextMessages);
    setDraft("");
    setStatus("");
    setSending(true);
    try {
      const response = await sendAgentChat(nextMessages);
      setMessages([...nextMessages, { role: "assistant", content: response.message }]);
      const toolCount = response.tool_calls.length;
      setStatus(toolCount ? `Used ${toolCount} Hunter tool${toolCount === 1 ? "" : "s"}.` : "");
      if (response.mutated) await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setMessages([...nextMessages, { role: "assistant", content: message }]);
      setStatus("Chat request failed.");
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button className="chat-launcher" type="button" onClick={() => setOpen(true)} aria-label="Open Hunter chat">
        <ChatIcon size={22} />
      </button>
    );
  }

  return (
    <section className="chat-panel" aria-label="Hunter chat">
      <header className="chat-header">
        <div>
          <strong>Hunter</strong>
          <span>Local chat</span>
        </div>
        <button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="Close Hunter chat">
          <XIcon size={17} />
        </button>
      </header>

      <div className="chat-messages" ref={messagesRef}>
        {messages.map((message, index) => (
          <ChatMessage key={`${message.role}-${index}`} message={message} />
        ))}
        {sending ? <div className="chat-message assistant muted">Working...</div> : null}
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
          placeholder="Ask Hunter..."
          rows={2}
          disabled={sending}
        />
        <button className="button primary compact" type="submit" disabled={sending || !draft.trim()} aria-label="Send message">
          <SendIcon size={15} />
        </button>
      </form>
      <div className="chat-status">{status}</div>
    </section>
  );
}
