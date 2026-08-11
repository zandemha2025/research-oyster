-- The authored report. A research job's deliverable is not the pile of evidence rows;
-- it is the analyst's answer written from them. The model authors this via
-- write_research_synthesis and Oyster persists it here so export leads with the answer
-- and evidence becomes an appendix.
ALTER TABLE research_jobs ADD COLUMN IF NOT EXISTS synthesis JSONB;
