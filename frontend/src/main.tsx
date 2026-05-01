import { Component, type ErrorInfo, type ReactNode, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./app/App.tsx";
import "./styles/index.css";

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Engram render error:", error, info);
  }

  render() {
    if (this.state.error) {
      const err = this.state.error as Error;
      return (
        <div style={{ fontFamily: "monospace", padding: "2rem", color: "#e2e8f0" }}>
          <h2 style={{ color: "#f87171" }}>Engram failed to load</h2>
          <p style={{ color: "#94a3b8" }}>
            Check the browser console (F12) for details and report this at{" "}
            <a href="https://github.com/Jsakkos/engram/issues" style={{ color: "#22d3ee" }}>
              github.com/Jsakkos/engram/issues
            </a>
          </p>
          <pre style={{ background: "#1e293b", padding: "1rem", borderRadius: "0.5rem", overflowX: "auto", color: "#f87171" }}>
            {err.message}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </StrictMode>
);
