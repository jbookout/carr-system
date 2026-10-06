-- Preserve approved private placeholder facts without publishing contact literals.
insert into public.system_config(key, value, note)
select 'contacts.protected_phone_numbers',
       jsonb_agg(distinct regexp_replace(found.phone[1], '[^0-9]', '', 'g')),
       'Protected contact placeholders seeded from the approved own-contact rule.'
  from public.rule r
  cross join lateral regexp_matches(r.statement,
    '(?:\+?1[ .-]?)?\(?[0-9]{3}\)?[ .-][0-9]{3}[ .-][0-9]{4}', 'g') as found(phone)
 where r.id::text like '54e2bcb9-%' and r.status = 'active'
having count(*) > 0
on conflict (key) do update
  set value = (select jsonb_agg(distinct phone)
                 from jsonb_array_elements(system_config.value || excluded.value) as phones(phone)),
      note = excluded.note,
      updated_at = now();
