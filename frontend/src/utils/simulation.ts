import type { SimulationRecording, SimVehicle } from '../types';

const turn = (from: number, to: number) => ((to - from + 540) % 360) - 180;

export function makeSampler(recording: SimulationRecording): (time: number) => SimVehicle[] {
  const frames = recording.frames.map(({ time, vehicles }) => ({
    time, vehicles, byId: new Map(vehicles.map((car) => [car[0], car])),
  }));
  return (time) => {
    let lo = 0;
    let hi = frames.length - 1;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (frames[mid].time <= time) lo = mid;
      else hi = mid - 1;
    }
    const frame = frames[lo];
    if (!frame) return [];
    const next = frames[lo + 1];
    if (!next) return frame.vehicles;
    const span = next.time - frame.time;
    if (span > 2) return time - frame.time < 1 ? frame.vehicles : [];
    const ratio = Math.max(0, Math.min(1, (time - frame.time) / span));
    return frame.vehicles.map((car) => {
      const [id, lon, lat, angle, kind] = car;
      const target = next.byId.get(id);
      if (!target) return car;
      return [id, lon + turn(lon, target[1]) * ratio, lat + (target[2] - lat) * ratio,
        angle + turn(angle, target[3]) * ratio, kind];
    });
  };
}
