document.addEventListener('DOMContentLoaded', () => {
    let currentKalman = 0;
    let currentWeight = 0;

    const elWeight = document.getElementById('live-weight');
    const elRaw = document.getElementById('live-raw');
    const elKalman = document.getElementById('live-kalman');
    const lockOverlay = document.getElementById('mixer-lock-overlay');

    const inCa = document.getElementById('in-ca');
    const inCc = document.getElementById('in-cc');
    const inCb = document.getElementById('in-cb');
    const inCups = document.getElementById('in-cups');
    const stdWeight = document.getElementById('std-weight');

    // 1. Загрузка сохраненного конфига
    async function loadConfig() {
        try {
            let res = await fetch('/api/scales/config');
            let conf = await res.json();
            inCa.value = conf.coeff_a || 400.0;
            inCc.value = conf.coeff_center || 400.0;
            inCb.value = conf.coeff_b || 400.0;
            inCups.value = conf.cups_weight_g || 0.0;
        } catch (e) {
            console.error("Ошибка загрузки конфига");
        }
    }

    // 2. Живой опрос датчика (через прокси миксера)
    async function fetchLive() {
        try {
            let res = await fetch('/api/scales/live');
            let data = await res.json();
            
            // Если миксер льет — выбрасываем блокировку экрана
            if (data.locked) {
                lockOverlay.style.display = 'flex';
                return;
            } else {
                lockOverlay.style.display = 'none';
            }

            currentWeight = data.weight;
            currentKalman = data.kalman;
            
            elWeight.textContent = data.weight.toFixed(2);
            elRaw.textContent = data.raw;
            elKalman.textContent = data.kalman;
        } catch (e) {
            elWeight.textContent = "ERR";
        }
    }

    // 3. Сохранение конфига
    document.getElementById('btn-save').addEventListener('click', async () => {
        let conf = {
            coeff_a: parseFloat(inCa.value),
            coeff_center: parseFloat(inCc.value),
            coeff_b: parseFloat(inCb.value),
            cups_weight_g: parseFloat(inCups.value),
            median_window: 3,
            kalman_err_measure: 100,
            kalman_err_estimate: 10,
            kalman_q: 0.1
        };
        try {
            let res = await fetch('/api/scales/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(conf)
            });
            if (res.ok) alert("Настройки успешно сохранены!");
            else alert("Ошибка сохранения");
        } catch (e) {
            alert("Ошибка связи");
        }
    });

    // 4. Кнопка Тары
    document.getElementById('btn-tare').addEventListener('click', async () => {
        if (!confirm("Внимание! Снимите все грузы с платформы. Сбросить в 0?")) return;
        elWeight.textContent = "...";
        try {
            await fetch('/api/scales/tare', { method: 'POST' });
        } catch (e) {
            alert("Ошибка команды тары");
        }
    });

    // 5. Автокалибровка коэффициентов (Новый коэфф = Показания Калмана / Реальный вес эталона)
    function calibrateZone(inputId) {
        let std = parseFloat(stdWeight.value);
        if (!std || std <= 0) {
            alert("Введите корректный вес эталона!");
            return;
        }
        let newCoeff = currentKalman / std;
        document.getElementById(inputId).value = newCoeff.toFixed(2);
    }

    document.getElementById('btn-cal-a').onclick = () => calibrateZone('in-ca');
    document.getElementById('btn-cal-c').onclick = () => calibrateZone('in-cc');
    document.getElementById('btn-cal-b').onclick = () => calibrateZone('in-cb');

    // 6. Фиксация тары чашек
    document.getElementById('btn-fix-cups').onclick = () => {
        inCups.value = currentWeight.toFixed(2);
    };

    loadConfig();
    setInterval(fetchLive, 400); // Опрашиваем 2.5 раза в секунду
});