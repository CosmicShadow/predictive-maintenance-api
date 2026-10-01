using './main.bicep'

// Values come from environment variables so the same file works on your laptop and in
// GitHub Actions. Nothing secret is stored in this file.
param prefix = readEnvironmentVariable('PREFIX', 'rulpm')
param sqlLocation = readEnvironmentVariable('SQL_LOCATION', 'centralus')
param sqlAdminLogin = readEnvironmentVariable('SQL_ADMIN_LOGIN')
param sqlAdminObjectId = readEnvironmentVariable('SQL_ADMIN_OBJECT_ID')
param dataScientistPrincipalId = readEnvironmentVariable('DATA_SCIENTIST_PRINCIPAL_ID', '')
param alertEmail = readEnvironmentVariable('ALERT_EMAIL')
param apiKey = readEnvironmentVariable('API_KEY')
param containerImage = readEnvironmentVariable('CONTAINER_IMAGE', '')
param clientIpAddress = readEnvironmentVariable('CLIENT_IP_ADDRESS', '')
