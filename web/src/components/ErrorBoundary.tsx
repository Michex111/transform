/**
 * A render-error boundary for third-party UI that can throw.
 *
 * React unmounts the whole tree above a render error when nothing catches it. On
 * the Billing page that is the worst possible outcome: the customer clicked into
 * card management, Stripe.js rejected something, and they are left with a blank
 * screen instead of the page that explains and fixes the problem.
 *
 * This exists specifically for Stripe's `<Elements>`. Verified in a browser:
 * handing Stripe a malformed client secret makes its constructor throw
 * (`IntegrationError: clientSecret should be a client secret of the form …`)
 * during render, and the entire Billing page went empty. With this boundary the
 * card form simply fails on its own and the section can say so.
 *
 * The fallback is deliberately the caller's business — the section renders its
 * own explanation, and uses `onError` to offer a route that still works.
 */

import { Component, type ReactNode } from "react";

export interface ErrorBoundaryProps {
  /** What to render instead of the crashed subtree. */
  fallback: ReactNode;
  /** Called once when a render error is caught, so the parent can adapt. */
  onError?: (error: Error) => void;
  /**
   * Reset the boundary when this value changes.
   *
   * Without it a single failure is permanent for the life of the mount, even
   * after the condition that caused it is gone. The section ties this to its
   * open/closed form state, so reopening the form tries again.
   */
  resetKey?: unknown;
  children: ReactNode;
}

interface ErrorBoundaryState {
  failed: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): Partial<ErrorBoundaryState> {
    return { failed: true };
  }

  // React requires one of the two error hooks to be present for this to be a
  // boundary at all; this is where the parent is told. The component stack is
  // not forwarded: the caller's job is to degrade, not to log Stripe internals.
  componentDidCatch(error: Error): void {
    this.props.onError?.(error);
  }

  componentDidUpdate(previous: ErrorBoundaryProps): void {
    // A changed `resetKey` clears the failure, so the next render re-attempts
    // the subtree rather than leaving the fallback in place forever. Guarded on
    // `failed` so this cannot loop.
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  render(): ReactNode {
    if (this.state.failed) return this.props.fallback;
    return this.props.children;
  }
}
