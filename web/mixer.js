document.addEventListener('DOMContentLoaded', () => {
    const btnStart = document.getElementById('btn-start');
    const btnStop = document.getElementById('btn-stop');
    const btnFlush = document.getElementById('btn-flush');
    const btnSaveStock = document.getElementById('btn-save-stock');
    const badge = document.getElementById('mixer-status-badge');
    const errAlert = document.getElementById('error-alert');
    
    const taskName = document.getElementById('active-task-name');
    const tasksLeft = document.getElementById('tasks-left');
    const wTotal = document.getElementById('w-total');
    const weightA = document.getElementById('w-a');
    const weightB = document.getElementById('w-b');
    const cupsStatus = document.getElementById('cups-status');

    let isUserEditingStock = false;
    let stockBlurTimeout;

    // Запрещаем обновлять инвентарь с сервера, если юзер кликнул в поле
    document.querySelectorAll('.stock-in').forEach(el => {
        el.addEventListener('focus', () => {
            clearTimeout(stockBlurTimeout);
            isUserEditingStock = true;
        });
        el.addEventListener('blur', () => {
            // Задержка перед снятием флага, чтобы событие click на кнопке "Сохранить"
            // успело прочитать введенные данные до того, как fetchStatus их перезапишет.
            stockBlurTimeout = setTimeout(() => { 
                isUserEditingStock = false; 
            }, 400);
        });
    });

    function toggleInputs(disabled) {
        document.querySelectorAll('.p-in, .stock-in, #in-precision').forEach(el => el.disabled = disabled);
        btnSaveStock.disabled = disabled;
    }

    function highlightRow(pumpId) {
        document.querySelectorAll('.pump-row').forEach(el => el.classList.remove('pump-active'));
        if (pumpId) {
            const row = document.getElementById(`row-${pumpId}`);
            if (row) row.classList.add('pump-active');
        }
    }

    btnSaveStock.addEventListener('click', async () => {
        // Принудительно держим блокировку UI на время отправки запроса
        clearTimeout(stockBlurTimeout);
        isUserEditingStock = true;
        
        const payload = {};
        for (let i = 1; i <= 8; i++) {
            payload[`p${i}`] = parseFloat(document.getElementById(`st-p${i}`).value) || 0;
        }
        
        btnSaveStock.textContent = "Сохранение...";
        try {
            await fetch('/api/mixer/update_stock', { 
                method: 'POST', 
                headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify(payload) 
            });
        } catch (e) {
            console.error("Ошибка сохранения остатков", e);
        }
        
        setTimeout(() => { 
            btnSaveStock.textContent = "💾 Сохранить"; 
            // Снимаем блокировку только после того, как сервер гарантированно принял данные
            isUserEditingStock = false; 
        }, 500);
    });

    async function fetchStatus() {
        try {
            const res = await fetch('/api/mixer/status');
            const data = await res.json();
            
            wTotal.textContent = data.weight_total.toFixed(2);
            weightA.textContent = data.weight_a.toFixed(2);
            weightB.textContent = data.weight_b.toFixed(2);
            tasksLeft.textContent = data.tasks_left;

            if (data.cups_present) cupsStatus.innerHTML = '<span style="color: #38a169;">✅ Чашки на месте</span>';
            else cupsStatus.innerHTML = '<span style="color: #e53e3e;">⚠️ ВНИМАНИЕ: Нет чашек (или не откалиброваны)!</span>';

            if (data.loaded_pumps && Object.keys(data.loaded_pumps).length > 0) {
                for (let i = 1; i <= 8; i++) {
                    const val = data.loaded_pumps[`p${i}`];
                    const inputEl = document.getElementById(`in-p${i}`);
                    if (inputEl) {
                        inputEl.value = val !== undefined ? val.toFixed(2) : "0.00";
                    }
                }
                fetch('/api/mixer/clear_loaded', { method: 'POST' }).catch(console.error);
            }

            // Обновляем инвентарь с сервера, только если юзер сам не печатает там сейчас
            if (data.stocks && !isUserEditingStock) {
                for (let i = 1; i <= 8; i++) {
                    const stockVal = data.stocks[`p${i}`];
                    const inputEl = document.getElementById(`st-p${i}`);
                    if (inputEl) {
                        inputEl.value = (stockVal !== undefined) ? stockVal.toFixed(2) : "0.00";
                    }
                }
            }

            if (!data.cups_present && !data.is_dosing) btnStart.disabled = true;
            else if (!data.is_dosing && data.tasks_left === 0) btnStart.disabled = false;

            if (data.error) {
                errAlert.style.display = 'block';
                errAlert.textContent = data.error;
                badge.className = 'status-badge status-error';
                badge.textContent = 'ОШИБКА';
                toggleInputs(false);
                highlightRow(null);
            } else if (data.is_dosing || data.tasks_left > 0) {
                errAlert.style.display = 'none';
                badge.className = 'status-badge status-running';
                badge.textContent = 'НАЛИВ';
                btnStart.disabled = true;
                toggleInputs(true);
                if (data.current_task) {
                    taskName.textContent = `${data.current_task.name} -> ${data.current_task.amount_g.toFixed(2)} г`;
                    highlightRow(data.current_task.pump_id);
                } else {
                    taskName.textContent = "Смена шага...";
                }
            } else {
                errAlert.style.display = 'none';
                badge.className = 'status-badge status-idle';
                badge.textContent = 'ОЖИДАНИЕ';
                taskName.textContent = 'Нет активных задач';
                toggleInputs(false);
                highlightRow(null);
            }
        } catch (e) {
            badge.className = 'status-badge status-error';
            badge.textContent = 'НЕТ СВЯЗИ';
        }
    }

    btnStart.addEventListener('click', async () => {
        const payload = { precision: parseFloat(document.getElementById('in-precision').value) || 0.1 };
        let hasValues = false;
        let missingStockErrors = [];

        for (let i = 1; i <= 8; i++) {
            const val = parseFloat(document.getElementById(`in-p${i}`).value) || 0;
            
            if (val > 0) {
                const stockVal = parseFloat(document.getElementById(`st-p${i}`).value) || 0;
                
                if (val > stockVal) {
                    const pumpNameEl = document.querySelector(`#row-p${i} .text-secondary`);
                    const pumpName = pumpNameEl ? pumpNameEl.innerText : `Помпа ${i}`;
                    missingStockErrors.push(`- ${pumpName} (Нужно: ${val.toFixed(2)}г, Остаток: ${stockVal.toFixed(2)}г)`);
                }
                
                payload[`p${i}`] = val;
                hasValues = true;
            }
        }

        if (!hasValues) {
            alert('Введите вес хотя бы для одной помпы!');
            return;
        }

        if (missingStockErrors.length > 0) {
            alert('ОТМЕНА СТАРТА! Недостаточно раствора в баках:\n\n' + 
                  missingStockErrors.join('\n') + 
                  '\n\nДолейте раствор, обновите значения остатков кнопкой "Сохранить", и попробуйте снова.');
            return;
        }

        try {
            const res = await fetch('/api/mixer/start_manual', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
            if (!res.ok) {
                const errText = await res.text();
                errAlert.style.display = 'block';
                errAlert.textContent = errText;
            }
        } catch (e) { alert('Ошибка отправки команды'); }
    });

    btnFlush.addEventListener('click', async () => {
        if (!confirm("Вы слили жидкость обратно в бак? Память налитого в кружки будет обнулена!")) return;
        try { await fetch('/api/mixer/flush', { method: 'POST' }); } catch (e) {}
    });

    btnStop.addEventListener('click', async () => {
        try { await fetch('/api/mixer/stop', { method: 'POST' }); } catch (e) {}
    });

    setInterval(fetchStatus, 400);
});