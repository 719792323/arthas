/**
 * 全局错误边界组件
 * 防止子组件渲染异常导致整个应用白屏
 */

import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: string;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: '' };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] 捕获到渲染异常:', error, info);
    this.setState({
      errorInfo: info.componentStack || '',
    });
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null, errorInfo: '' });
    window.location.reload();
  };

  handleRetry = () => {
    this.setState({ hasError: false, error: null, errorInfo: '' });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-gradient-to-br from-slate-50 to-red-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl border border-red-200 max-w-lg w-full p-8 text-center">
            <div className="text-6xl mb-4">💥</div>
            <h1 className="text-xl font-bold text-slate-800 mb-2">页面渲染出错</h1>
            <p className="text-sm text-slate-500 mb-4">
              应用遇到了一个意外错误，可能是后端服务未启动或网络问题导致的。
            </p>

            {this.state.error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-left">
                <p className="text-xs font-mono text-red-600 break-words">
                  {this.state.error.message}
                </p>
              </div>
            )}

            {this.state.errorInfo && (
              <details className="mb-4 text-left">
                <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-600">
                  查看组件堆栈
                </summary>
                <pre className="mt-2 text-xs bg-slate-900 text-slate-300 p-3 rounded-lg overflow-auto max-h-40 font-mono whitespace-pre-wrap">
                  {this.state.errorInfo}
                </pre>
              </details>
            )}

            <div className="flex items-center justify-center space-x-3">
              <button
                onClick={this.handleRetry}
                className="px-5 py-2.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors text-sm font-medium"
              >
                🔄 重试
              </button>
              <button
                onClick={this.handleReload}
                className="px-5 py-2.5 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-lg transition-colors text-sm font-medium"
              >
                🔃 刷新页面
              </button>
            </div>

            <p className="mt-6 text-xs text-slate-400">
              提示：请确保后端服务运行在 <code className="bg-slate-100 px-1 rounded">localhost:8080</code>
            </p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
