import { createContext, useContext, type ReactNode } from "react";

export type Employee = {
  id: string;
  name: string;
  email: string;
  role: string;
  role_label: string;
  permissions: string[];
};
const AuthContext = createContext<Employee | null>(null);
export function AuthProvider({
  user,
  children,
}: {
  user: Employee;
  children: ReactNode;
}) {
  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}
export function useAccess() {
  const user = useContext(AuthContext);
  return {
    user,
    can: (permission: string) =>
      Boolean(user?.permissions.includes(permission)),
  };
}
