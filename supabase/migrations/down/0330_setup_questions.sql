-- Down for 0330 setup: drops assumptions and the letter-reading record.
set role app_migrator;
drop table if exists app.instruction_extractions;
drop table if exists app.assumptions;
drop function if exists app.assumptions_guard();
reset role;
