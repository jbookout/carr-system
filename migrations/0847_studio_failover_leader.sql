-- Source only: manual host failover is not enabled by applying this schema.
create table ops.studio_leader (
    singleton boolean primary key default true check (singleton),
    host text not null check (host in ('studio','macbook')),
    epoch bigint not null default 1 check (epoch > 0),
    fence_evidence jsonb not null default '{}'::jsonb,
    changed_at timestamptz not null default clock_timestamp()
);
insert into ops.studio_leader(singleton,host) values (true,'studio');
revoke all on ops.studio_leader from public;
grant usage on schema ops to carr_jobs,carr_authority_joe;
grant select on ops.studio_leader to carr_jobs;
grant select,update on ops.studio_leader to carr_authority_joe;
comment on table ops.studio_leader is
  'Durable Studio/MacBook owner. Never expires. Manual transfer requires source fencing '
  'and exclusive advisory lock 638148226000001; guarded jobs hold its shared form.';
