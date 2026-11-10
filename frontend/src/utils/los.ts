import type { Los } from '../types';

/** ODM 218.2.020-2012, table 1. The upper boundary of E is inclusive. */
export function levelOfService(vc: number): Los {
  if (vc < 0.20) return 'A';
  if (vc < 0.45) return 'B';
  if (vc < 0.70) return 'C';
  if (vc < 0.90) return 'D';
  return vc <= 1 ? 'E' : 'F';
}
