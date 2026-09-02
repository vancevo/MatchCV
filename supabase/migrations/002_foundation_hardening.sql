-- Prevent two interview bookings for the same recruiter at the same instant.
create unique index if not exists interviews_owner_start_unique_idx
    on public.interviews(owner_id, start_at);
