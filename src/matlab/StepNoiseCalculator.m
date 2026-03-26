M = readmatrix('Sample step noise data.xlsx');
%M = readmatrix('Sample step noise data control.xlsx');
t = M(:,1);
s = M(:,2);

% Compute upper and lower envelopes
[up, lo] = envelope(s, 50, 'peak'); % '20' is the peak separation distance

% Plot the results
figure(1)
clf
plot(t, s, '.', 'Color', [0.7 0.7 0.7]); % Original data
title('Time-Domain Signal')
xlabel('Time (s)')
ylabel('Amplitude (mV)')
xlim([60 95])
hold on;
plot(t, up, 'r', 'LineWidth', 2); % Upper fit
plot(t, lo, 'b', 'LineWidth', 2); % Lower fit

l = length(t);
avg = mean(s);
% hug is an array containing whichever distance from a data point to the
% corresponding up or lo value is smaller
% mid is just the distance to the average s value
%pre-allocate size to make assigning values faster
Hug = zeros(1, l);
Mid = zeros(1, l);

%% For all s compute distance to up, distance to lo, and distance to mean
for i = 1:l
    dTop = abs(s(i)-up(i));
    dBot = abs(s(i)-lo(i));
    dMid = abs(s(i)-avg);
    Mid(i) = dMid; %store dist as ith element of Mid

    if dTop < dBot
        Hug(i) = dTop;
    else
        Hug(i) = dBot;
    end
end

%compute mean values of Hug and Mid, if Hug is smaller than Mid, the values
%prefer the edges, meaning step  noise is present
mHug = mean(Hug);
mMid = mean(Mid);
disp(['The mean hug value is ',num2str(mHug)])
disp(['The mean distance to the average is ',num2str(mMid)])
disp(['The ratio hug/mean is ',num2str(mHug/mMid)])


figure(2)
clf
plot(t,s)
title('Time-Domain Signal')
xlabel('Time (s)')
ylabel('Amplitude (mV)')
xlim([60 95])