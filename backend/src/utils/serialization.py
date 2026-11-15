import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled','retention_days']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination',
            'blob_id','retention_days','expires_at','held','held_at','held_by','hold_reason',
            'state','cleaned_at','cleanup_run_id','last_error','size_bytes']}
def blob_dict(b):
    return {k:getattr(b,k) for k in ['id','checksum','size_bytes','ref_count','orphan','reaped','reaped_at','created_at','last_referenced_at']}
def cleanup_run_dict(r):
    return {k:getattr(r,k) for k in ['id','triggered_by','started_at','finished_at','status','last_delivery_id',
            'total_candidates','cleaned_count','held_count','missing_count','skipped_count','failed_count',
            'reclaimed_bytes','last_error']}
def cleanup_item_dict(i):
    return {k:getattr(i,k) for k in ['id','run_id','delivery_id','result','detail','reclaimed_bytes','processed_at']}
