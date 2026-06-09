/**
 * Re-exporta el hook useAuth desde AuthContext para acceso conveniente.
 * Uso: import { useAuth } from '@/hooks/useAuth';
 */
export { useAuth } from '../context/AuthContext';
export type { AuthContextType, AuthState, AuthUser } from '../context/AuthContext';
