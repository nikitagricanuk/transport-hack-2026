import assert from 'node:assert/strict';
import { test } from 'node:test';
import { vehicleModels } from './vehicleModels.ts';

test('cars follow compass headings and retain dimensions, height and identity across frames', () => {
  for (const heading of [0,90,180,270]) {
    const models = vehicleModels([['car',104,52,heading]]);
    const front = models.features.find(f=>f.properties!.part === 'headlight:0.58')!;
    const center = front.geometry.coordinates[0].slice(0,4).reduce((a,p)=>[a[0]+p[0]/4,a[1]+p[1]/4],[0,0]);
    const forward = (center[0]-104)*111320*Math.cos(52*Math.PI/180)*Math.sin(heading*Math.PI/180)
      +(center[1]-52)*110540*Math.cos(heading*Math.PI/180);
    assert.ok(Math.abs(forward-2.16)<0.001);
    assert.ok(models.features.every(f=>f.properties!.height>f.properties!.base && f.properties!.vehicleId==='car'));
    assert.equal(models.features.filter(f=>String(f.properties!.part).startsWith('wheel')).length,4);
  }
  const a=vehicleModels([['car',104,52,0]]), b=vehicleModels([['car',105,53,90]]);
  assert.deepEqual(a.features.map(f=>f.id),b.features.map(f=>f.id));
  assert.deepEqual(a.features.map(f=>f.properties!.color),b.features.map(f=>f.properties!.color));
  assert.equal(vehicleModels([]).features.length,0);
});
