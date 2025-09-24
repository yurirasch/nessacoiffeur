/***** CONFIG *****/
const CFG = {
  DB_APPTS: 'DB_AGENDAMENTOS',
  DB_CLIENTS: 'CLIENTES',
  DB_SERVICES: 'SERVICOS',
  DB_EMPLOYEES: 'FUNCIONARIOS',
  DB_BLOCKS: 'BLOQUEIOS',
  COL_TIME: 3,   // C = hora
  COL_NAME_LU: 4, COL_SERV_LU: 5, // D/E
  COL_NAME_MA: 6, COL_SERV_MA: 7, // F/G
  COL_NAME_TI: 8, COL_SERV_TI: 9, // H/I
  COL_DATE_HELPER: 27, // AA = 27
};

function onEdit(e) {
  try {
    const sh = e.range.getSheet();
    const sheetName = sh.getName();
    if (['CLIENTES','SERVICOS','FUNCIONARIOS','DB_AGENDAMENTOS','BLOQUEIOS'].includes(sheetName)) return;
    const row = e.range.getRow();
    const col = e.range.getColumn();
    if (row < 5) return;

    let employeeName = null, serviceValue = null, nameValue = null;
    if (col === CFG.COL_NAME_LU || col === CFG.COL_SERV_LU) {
      employeeName = 'LUCIENE';
      nameValue = sh.getRange(row, CFG.COL_NAME_LU).getDisplayValue().trim();
      serviceValue = sh.getRange(row, CFG.COL_SERV_LU).getDisplayValue().trim();
    } else if (col === CFG.COL_NAME_MA || col === CFG.COL_SERV_MA) {
      employeeName = 'MARCELA';
      nameValue = sh.getRange(row, CFG.COL_NAME_MA).getDisplayValue().trim();
      serviceValue = sh.getRange(row, CFG.COL_SERV_MA).getDisplayValue().trim();
    } else if (col === CFG.COL_NAME_TI || col === CFG.COL_SERV_TI) {
      employeeName = 'TINA';
      nameValue = sh.getRange(row, CFG.COL_NAME_TI).getDisplayValue().trim();
      serviceValue = sh.getRange(row, CFG.COL_SERV_TI).getDisplayValue().trim();
    } else {
      return;
    }
    if (!nameValue && !serviceValue) return;

    const dateVal = sh.getRange(row, CFG.COL_DATE_HELPER).getValue();
    if (!dateVal) return;
    const hora = sh.getRange(row, CFG.COL_TIME).getDisplayValue().trim();

    const ss = SpreadsheetApp.getActive();
    const wbEmp = ss.getSheetByName(CFG.DB_EMPLOYEES);
    const empVals = wbEmp.getDataRange().getValues();
    const empHeader = empVals.shift();
    const idxEmpName = empHeader.indexOf('name');
    const idxEmpId = empHeader.indexOf('employee_id');
    let empRow = empVals.find(r => String(r[idxEmpName]).toUpperCase() === employeeName);
    if (!empRow) throw new Error('Funcionário não encontrado: ' + employeeName);
    const employeeId = empRow[idxEmpId];

    let serviceId = '', serviceName = '';
    let defaultDur = 60;
    if (serviceValue) {
      const wbSvc = ss.getSheetByName(CFG.DB_SERVICES);
      const svcVals = wbSvc.getDataRange().getValues();
      const svcHeader = svcVals.shift();
      const iName = svcHeader.indexOf('name');
      const iId = svcHeader.indexOf('service_id');
      const iDur = svcHeader.indexOf('default_duration_min');
      const iAct = svcHeader.indexOf('active');
      const match = svcVals.find(r => String(r[iName]).toUpperCase() === serviceValue.toUpperCase() && String(r[iAct]).toUpperCase() !== 'FALSE');
      if (match) {
        serviceId = match[iId];
        serviceName = match[iName];
        defaultDur = Number(match[iDur]) || 60;
      } else {
        serviceName = serviceValue; // aceitamos texto livre
        defaultDur = 60;
      }
    }

    // cliente: Nome - Telefone | Nome / Telefone | Nome | Telefone
    let clientName = nameValue, clientPhone = '';
    [' - ', ' / ', ' | '].forEach(sep => {
      if (clientName.includes(sep)) {
        const parts = clientName.split(sep);
        clientName = parts[0].trim();
        clientPhone = parts.slice(1).join(sep).trim();
      }
    });

    const wbCli = ss.getSheetByName(CFG.DB_CLIENTS);
    const cliVals = wbCli.getDataRange().getValues();
    const cliHeader = cliVals.shift();
    const cId = cliHeader.indexOf('client_id');
    const cName = cliHeader.indexOf('name');
    const cPhone = cliHeader.indexOf('phone');
    const cCreated = cliHeader.indexOf('created_at');
    const cUpdated = cliHeader.indexOf('updated_at');
    let cliRow = null;
    if (clientPhone) {
      const clean = clientPhone.replace(/\D/g,'');
      cliRow = cliVals.find(r => String(r[cPhone]).replace(/\D/g,'') === clean);
    }
    if (!cliRow && clientName) {
      cliRow = cliVals.find(r => String(r[cName]).toUpperCase() === clientName.toUpperCase());
    }
    let clientId = '';
    if (!cliRow && clientName) {
      clientId = 'C' + Date.now();
      wbCli.appendRow([clientId, clientName, clientPhone, new Date(), new Date()]);
    } else if (cliRow) {
      clientId = cliRow[cId];
      // Atualiza telefone se vazio
      if (!cliRow[cPhone] && clientPhone) {
        const idx = cliVals.indexOf(cliRow) + 2;
        wbCli.getRange(idx, cPhone+1).setValue(clientPhone);
        wbCli.getRange(idx, cUpdated+1).setValue(new Date());
      }
    }

    const start = new Date(dateVal);
    const [hh, mm] = hora.split(':').map(n => parseInt(n,10));
    start.setHours(hh); start.setMinutes(mm); start.setSeconds(0);
    const end = new Date(start.getTime() + defaultDur*60000);
    const endStr = Utilities.formatDate(end, Session.getScriptTimeZone(), 'HH:mm');

    const wbAp = ss.getSheetByName(CFG.DB_APPTS);
    const apptId = 'A' + Date.now();
    wbAp.appendRow([
      apptId,
      Utilities.formatDate(start, Session.getScriptTimeZone(), 'yyyy-MM-dd'),
      Utilities.formatDate(start, Session.getScriptTimeZone(), 'HH:mm'),
      defaultDur,
      endStr,
      employeeId,
      employeeName,
      clientId,
      clientName,
      clientPhone,
      serviceId,
      serviceName,
      sheetName,
      row,
      'booked',
      new Date(),
      Session.getActiveUser().getEmail() || 'sheet',
      '',
      '',
      '',
      ''
    ]);

    e.range.setBackground('#E3FCEF'); // feedback
    sh.getRange(row, 28).setValue(employeeName); // AB: funcionário para auditoria

  } catch (err) {
    SpreadsheetApp.getActive().toast('Erro onEdit: ' + err.message, 'Agenda', 5);
  }
}
