import { Component, type ErrorInfo, type ReactNode } from "react";

/** Keeps a failing widget (e.g. a PDF the browser can't parse) from taking down the page. */
export class ErrorBoundary extends Component<{ fallback: ReactNode; resetKey?: unknown; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(prev: { resetKey?: unknown }) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn("UI section failed to render", error.message, info.componentStack?.split("\n")[1]);
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
