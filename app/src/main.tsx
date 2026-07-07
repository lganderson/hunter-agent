import React from "react";
import { createRoot, type Root as ReactRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { useHunterData } from "./core/useHunterData";
import "./styles.css";

declare global {
  var hunterRoot: ReactRoot | undefined;
}

function Root() {
  const { data, error, refresh } = useHunterData();

  if (error) {
    return (
      <main className="main">
        <article className="panel">
          <div className="empty-state" style={{ display: "block" }}>
            Could not load Hunter data. Start the local app server with: make serve-app. {error}
          </div>
        </article>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="main">
        <article className="panel">
          <div className="empty-state" style={{ display: "block" }}>Loading Hunter...</div>
        </article>
      </main>
    );
  }

  return <App data={data} refresh={refresh} />;
}

globalThis.hunterRoot ??= createRoot(document.getElementById("root") as HTMLElement);
globalThis.hunterRoot.render(
  <React.StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </React.StrictMode>
);
