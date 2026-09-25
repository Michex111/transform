import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/api/client";
import type { UserResponse } from "@/api/types";

interface AuthContextValue {
  user: UserResponse | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  /**
   * Create an account. Resolves with the created (unverified) user.
   *
   * It resolves rather than throwing on success even though it does NOT sign
   * the user in — see the implementation. Callers must show the "check your
   * inbox" state; the user is not authenticated afterwards.
   */
  register: (data: {
    username: string;
    email: string;
    password: string;
    first_name?: string | null;
    last_name?: string | null;
  }) => Promise<UserResponse>;
  logout: () => void;
  /**
   * Replace the cached user with a freshly returned record.
   *
   * The profile endpoints all answer with the updated `UserResponse`, so a
   * caller that already holds one should publish it rather than re-fetching —
   * one round trip, and the shell (name, initials, avatar) updates on the same
   * tick as the form that changed it. Use {@link refreshUser} when only a
   * mutation's side effects are known and the record is not.
   */
  setUser: (user: UserResponse) => void;
  /**
   * Re-read `/users/me` and publish the result.
   *
   * Used after an action that changes something the caller cannot see — e.g.
   * a phone verification, which the server records on the user but does not
   * return as a full user object everywhere.
   */
  refreshUser: () => Promise<void>;
  api: typeof api;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Load current user on mount if a token exists.
  useEffect(() => {
    let active = true;
    async function load() {
      if (!api.isAuthenticated()) {
        setIsLoading(false);
        return;
      }
      try {
        const me = await api.me();
        if (active) setUser(me);
      } catch {
        api.clearTokens();
      } finally {
        if (active) setIsLoading(false);
      }
    }
    load();

    // When a token expires, bounce the user out.
    const onUnauthorized = () => {
      api.clearTokens();
      setUser(null);
    };
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => {
      active = false;
      window.removeEventListener("auth:unauthorized", onUnauthorized);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    await api.login(username, password);
    const me = await api.me();
    setUser(me);
  }, []);

  const register = useCallback(
    async (data: {
      username: string;
      email: string;
      password: string;
      first_name?: string | null;
      last_name?: string | null;
    }): Promise<UserResponse> => {
      // Intentionally does NOT sign the user in. Sign-in is refused until the
      // address is verified (403 EMAIL_NOT_VERIFIED), so chaining a login here
      // would throw and make a *successful* registration look like a failure —
      // and the user would land on a sign-in page they cannot pass. The caller
      // shows the "check your inbox" state instead.
      return api.register(data);
    },
    [],
  );

  const logout = useCallback(() => {
    api.clearTokens();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    // Guarded so a token that vanished mid-flight (another tab signed out)
    // cannot turn into an unauthenticated request that bounces the page.
    if (!api.isAuthenticated()) return;
    try {
      setUser(await api.me());
    } catch {
      // Leave the cached user in place. A failed refresh is a network problem
      // far more often than a revoked session, and the 401 path below already
      // handles the real case.
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      isLoading,
      isAuthenticated: Boolean(user),
      login,
      register,
      logout,
      setUser,
      refreshUser,
      api,
    }),
    [user, isLoading, login, register, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
