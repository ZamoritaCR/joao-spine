-- Cockpit energy state logging
-- Run in Supabase SQL Editor: https://supabase.com/dashboard/project/wkfewpynskakgbetscsa/sql

CREATE TABLE IF NOT EXISTS cockpit_energy_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  energy_level TEXT,
  scene_activated TEXT,
  source TEXT DEFAULT 'telegram'
);

ALTER TABLE cockpit_energy_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_cockpit_energy" ON cockpit_energy_log;
CREATE POLICY "service_all_cockpit_energy" ON cockpit_energy_log FOR ALL USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_cockpit_energy_created ON cockpit_energy_log (created_at DESC);
