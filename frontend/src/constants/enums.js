export const Role={admin:'业务负责人',analyst:'分析员',viewer:'观察员'};
export const JobStatus={queued:'排队中',running:'计算中',completed:'已完成',failed:'执行失败'};
export const DeliveryState={active:'在档',cleaned:'已清理',failed:'文件异常'};
export const CleanupStatus={running:'进行中',completed:'已完成',failed:'批次失败'};
export const CleanupItemResult={cleaned:'已释放引用',held:'保全跳过',missing:'文件缺失',skipped:'未到期/跳过',failed:'处理失败'};
export function bytes(v){
 if(v==null)return '—';
 if(v<1024)return v+' B';
 const units=['KB','MB','GB'];let n=v/1024,i=0;
 while(n>=1024&&i<units.length-1){n/=1024;i++;}
 return n.toFixed(n>=100?0:1)+' '+units[i];
}
