set role app_migrator;

drop function app.case_conflict_answers(uuid);
drop function app.case_conflict_declaration(uuid);
drop function app.case_signature(uuid);
drop function app.case_declarations(uuid);
drop function app.case_report_template(uuid);
drop function app.case_report_input_allowed(uuid, text);

reset role;
