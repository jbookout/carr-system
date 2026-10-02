insert into lead_stage(slug,label,sort) values ('archived','Archived',110)
on conflict (slug) do update set label=excluded.label,sort=excluded.sort;
