import React, { type ReactNode } from "react";

export type ErrorBoundaryProps = {
  children: ReactNode;
};

export type ErrorBoundaryState = {
  error: unknown;
};

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    error: null,
  };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo) {
    console.error("Lucode renderer error", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="app-error-boundary" role="alert">
          <section className="app-error-card">
            <div className="app-error-eyebrow">Lucode Renderer</div>
            <h1>界面渲染出错</h1>
            <p>{errorBoundaryMessage(this.state.error)}</p>
            <button className="primary-button" type="button" onClick={() => window.location.reload()}>
              重新加载
            </button>
          </section>
        </main>
      );
    }

    return this.props.children;
  }
}

export function errorBoundaryMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return `一个界面模块发生异常，已阻止整页白屏。错误信息：${error.message}`;
  }
  if (typeof error === "string" && error.trim()) {
    return `一个界面模块发生异常，已阻止整页白屏。错误信息：${error.trim()}`;
  }
  return "一个界面模块发生异常，已阻止整页白屏。请重新加载，或查看开发者控制台日志。";
}
