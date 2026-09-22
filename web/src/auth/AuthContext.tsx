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
  }) => Promise<UserResponse>;
  logout: () => void;
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

  const value = useMemo(
    () => ({
      user,
      isLoading,
      isAuthenticated: Boolean(user),
      login,
      register,
      logout,
      api,
    }),
    [user, isLoading, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
