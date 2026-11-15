import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={
 list:()=>api('/reports'),
 create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),
 toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),
 setRetention:(id,retention_days)=>api('/reports/'+id+'/retention',{method:'POST',body:JSON.stringify({retention_days})}),
 deliveries:(params='')=>api('/deliveries'+params),
 delivery:id=>api('/deliveries/'+id),
 hold:(id,hold,reason)=>api('/deliveries/'+id+'/hold',{method:'POST',body:JSON.stringify({hold,reason})}),
 setDeliveryRetention:(id,retention_days)=>api('/deliveries/'+id+'/retention',{method:'POST',body:JSON.stringify({retention_days})}),
 preview:()=>api('/retention/preview'),
 blobs:()=>api('/blobs'),
 cleanupRun:()=>api('/cleanup/run',{method:'POST'}),
 cleanupReap:()=>api('/cleanup/reap',{method:'POST'}),
 cleanupRuns:()=>api('/cleanup/runs'),
 cleanupRunDetail:id=>api('/cleanup/runs/'+id)
};
